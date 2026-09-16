import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import module_config


def _mock_db(existing=None):
    """A fake Firestore client whose one (deeply nested) document either
    exists (with `existing` as its data) or doesn't. MagicMock's .return_value
    is the same object regardless of call args, so
    db.collection(x).document(y).collection(z).document(w)... all resolve to
    one consistent chain we can configure once."""
    db = MagicMock()
    doc_ref = db.collection.return_value.document.return_value \
        .collection.return_value.document.return_value \
        .collection.return_value.document.return_value
    snapshot = MagicMock()
    if existing is None:
        snapshot.exists = False
    else:
        snapshot.exists = True
        snapshot.to_dict.return_value = existing
    doc_ref.get.return_value = snapshot
    return db, doc_ref


def test_get_module_config_creates_default_doc_when_missing():
    db, doc_ref = _mock_db(existing=None)
    with patch("app.firestore_db.get_firestore_client", return_value=db):
        cfg = module_config.get_module_config("t1", "e1", "health-check", {"enabled": False})
    assert cfg["enabled"] is False
    assert cfg["last_updated"] is None
    doc_ref.set.assert_called_once()


def test_get_module_config_merges_with_defaults():
    db, _ = _mock_db(existing={"enabled": True})
    with patch("app.firestore_db.get_firestore_client", return_value=db):
        cfg = module_config.get_module_config("t1", "e1", "health-check", {"enabled": False, "last_report": None})
    assert cfg["enabled"] is True          # stored value wins
    assert cfg["last_report"] is None       # default fills in missing keys


def test_get_module_config_falls_back_on_firestore_error():
    with patch("app.firestore_db.get_firestore_client", side_effect=RuntimeError("no creds")):
        cfg = module_config.get_module_config("t1", "e1", "health-check", {"enabled": True})
    assert cfg["enabled"] is True


def test_update_module_config_full_sets_merged_doc():
    db, doc_ref = _mock_db(existing={"enabled": False})
    with patch("app.firestore_db.get_firestore_client", return_value=db):
        updated = module_config.update_module_config(
            "t1", "e1", "url-mapping", {"enabled": True}, defaults={"enabled": False}
        )
    assert updated["enabled"] is True
    assert updated["last_updated"] is not None
    # update_config always .set()s the full merged document, never a partial update.
    doc_ref.set.assert_called_with(updated)


def test_is_module_enabled_defaults_true_for_license_sync():
    with patch("app.firestore_db.get_firestore_client", side_effect=RuntimeError("no creds")):
        assert module_config.is_module_enabled("t1", "e1", "license-sync") is True


def test_is_module_enabled_defaults_false_for_new_modules():
    with patch("app.firestore_db.get_firestore_client", side_effect=RuntimeError("no creds")):
        assert module_config.is_module_enabled("t1", "e1", "health-check") is False


def test_is_module_enabled_reflects_stored_value():
    db, _ = _mock_db(existing={"enabled": True})
    with patch("app.firestore_db.get_firestore_client", return_value=db):
        assert module_config.is_module_enabled("t1", "e1", "health-check") is True
