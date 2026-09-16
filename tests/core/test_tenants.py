import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.core import tenants


def _mock_db():
    """A fake Firestore client. MagicMock's .return_value is the same object
    regardless of call args, so chained .collection(x).document(y) calls all
    resolve to one consistent mock we can configure."""
    db = MagicMock()
    return db


def test_create_tenant_writes_expected_fields():
    db = _mock_db()
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        tenant_id = tenants.create_tenant("Acme Corp", created_by_uid="u1", primary_domain="acme.com")
    assert tenant_id
    doc_ref = db.collection.return_value.document.return_value
    doc_ref.set.assert_called_once()
    written = doc_ref.set.call_args[0][0]
    assert written["name"] == "Acme Corp"
    assert written["primary_domain"] == "acme.com"
    assert written["created_by_uid"] == "u1"


def test_get_tenant_returns_none_when_missing():
    db = _mock_db()
    snapshot = MagicMock(exists=False)
    db.collection.return_value.document.return_value.get.return_value = snapshot
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        assert tenants.get_tenant("t1") is None


def test_get_tenant_returns_data_with_id():
    db = _mock_db()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"name": "Acme Corp"}
    db.collection.return_value.document.return_value.get.return_value = snapshot
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        tenant = tenants.get_tenant("t1")
    assert tenant == {"id": "t1", "name": "Acme Corp"}


def test_add_member_rejects_invalid_role():
    db = _mock_db()
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        try:
            tenants.add_member("t1", "u1", "a@b.com", role="superuser")
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_add_member_dual_writes_membership_and_user_index():
    db = _mock_db()
    # user_index doc doesn't exist yet
    index_snapshot = MagicMock(exists=False)
    db.collection.return_value.document.return_value.get.return_value = index_snapshot

    with patch("app.core.tenants.get_firestore_client", return_value=db):
        tenants.add_member("t1", "u1", "a@b.com", role="owner", added_by_uid="u1")

    # Two distinct .collection() calls happened: members subcollection write,
    # and the top-level user_index write - both via the same mocked client.
    assert db.collection.call_count >= 2


def test_get_member_returns_none_when_missing():
    db = _mock_db()
    snapshot = MagicMock(exists=False)
    db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snapshot
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        assert tenants.get_member("t1", "u1") is None


def test_get_tenant_ids_for_uid_empty_when_no_index_doc():
    db = _mock_db()
    snapshot = MagicMock(exists=False)
    db.collection.return_value.document.return_value.get.return_value = snapshot
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        assert tenants.get_tenant_ids_for_uid("u1") == []


def test_create_environment_writes_onboarding_status():
    db = _mock_db()
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        environment_id = tenants.create_environment("t1", "Prod")
    assert environment_id
    doc_ref = db.collection.return_value.document.return_value \
        .collection.return_value.document.return_value
    written = doc_ref.set.call_args[0][0]
    assert written["display_name"] == "Prod"
    assert written["status"] == "onboarding"


def test_get_environment_returns_none_when_missing():
    db = _mock_db()
    snapshot = MagicMock(exists=False)
    db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snapshot
    with patch("app.core.tenants.get_firestore_client", return_value=db):
        assert tenants.get_environment("t1", "e1") is None


def test_update_environment_returns_merged_doc():
    db = _mock_db()
    doc_ref = db.collection.return_value.document.return_value \
        .collection.return_value.document.return_value
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"display_name": "Prod", "status": "active"}
    doc_ref.get.return_value = snapshot

    with patch("app.core.tenants.get_firestore_client", return_value=db):
        updated = tenants.update_environment("t1", "e1", {"status": "active"})

    doc_ref.update.assert_called_once_with({"status": "active"})
    assert updated["status"] == "active"
    assert updated["id"] == "e1"
    assert updated["tenant_id"] == "t1"
