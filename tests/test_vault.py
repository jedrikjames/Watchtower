"""The vault is the one component where a bug is a security bug."""

from __future__ import annotations

import json

import pytest

from watchtower.errors import StorageError, VaultLocked, VaultPassphraseError
from watchtower.paths import vault_file
from watchtower.secretstore.vault import FileVault


def make_vault(passphrase: str = "correct horse") -> FileVault:
    vault = FileVault()
    vault.create(passphrase)
    return vault


def test_round_trip():
    vault = make_vault()
    vault.put("account:1", "hello")
    vault.close()

    reopened = FileVault()
    reopened.unlock("correct horse")
    assert reopened.get("account:1") == "hello"


def test_wrong_passphrase_is_rejected():
    make_vault().close()
    with pytest.raises(VaultPassphraseError):
        FileVault().unlock("not the passphrase")


def test_locked_vault_refuses_reads():
    make_vault().close()
    with pytest.raises(VaultLocked):
        FileVault().get("account:1")


def test_ciphertext_does_not_contain_the_secret():
    vault = make_vault()
    vault.put("account:1", "super-secret-token-value")
    raw = vault_file().read_bytes()
    assert b"super-secret-token-value" not in raw
    assert b"account:1" not in raw, "key names should be inside the ciphertext too"


def test_nonce_changes_on_every_write():
    vault = make_vault()
    vault.put("a", "1")
    first = json.loads(vault_file().read_text())["nonce"]
    vault.put("b", "2")
    second = json.loads(vault_file().read_text())["nonce"]
    assert first != second


def test_tampering_is_detected():
    vault = make_vault()
    vault.put("account:1", "hello")

    envelope = json.loads(vault_file().read_text())
    # Flip a byte in the ciphertext.
    body = bytearray(envelope["ct"].encode())
    body[10] = body[10] + 1 if body[10] != ord("Z") else ord("A")
    envelope["ct"] = body.decode()
    vault_file().write_text(json.dumps(envelope))

    with pytest.raises((VaultPassphraseError, StorageError)):
        FileVault().unlock("correct horse")


def test_weak_kdf_parameters_are_refused():
    """A file claiming n=2 must not be opened, however valid its tag."""
    vault = make_vault()
    vault.put("account:1", "hello")

    envelope = json.loads(vault_file().read_text())
    envelope["n"] = 2
    vault_file().write_text(json.dumps(envelope))

    with pytest.raises(StorageError):
        FileVault().unlock("correct horse")


def test_changing_the_passphrase_keeps_the_contents():
    vault = make_vault()
    vault.put("account:1", "hello")
    vault.change_passphrase("a different one")
    vault.close()

    reopened = FileVault()
    reopened.unlock("a different one")
    assert reopened.get("account:1") == "hello"

    with pytest.raises(VaultPassphraseError):
        FileVault().unlock("correct horse")


def test_short_passphrase_is_rejected():
    with pytest.raises(VaultPassphraseError):
        FileVault().create("short")


def test_create_refuses_to_clobber():
    make_vault()
    with pytest.raises(StorageError):
        FileVault().create("another passphrase")


def test_delete_and_keys():
    vault = make_vault()
    vault.put("b", "2")
    vault.put("a", "1")
    assert vault.keys() == ["a", "b"]
    vault.delete("a")
    assert vault.keys() == ["b"]
    assert vault.get("a") is None
