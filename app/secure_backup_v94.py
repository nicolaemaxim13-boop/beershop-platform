"""v9.4 offline-only authenticated encryption for integrity-checked BeerShop archives.

Never expose plaintext or passwords to stdout, logs, URLs or persisted env vars.
This is a local file envelope, NOT automatic production backup scheduling.
"""
import os
import secrets
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b'BEERSHOP-V94-ENC\x00'
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
MAX_PLAINTEXT = 25 * 1024 * 1024
MAX_ENCRYPTED = len(MAGIC) + SALT_LEN + NONCE_LEN + MAX_PLAINTEXT + TAG_LEN


class UnsafeEncryptedBackup(ValueError):
    pass


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 20 or len(passphrase) > 1024:
        raise UnsafeEncryptedBackup('Backup passphrase must be 20–1024 characters')
    if not passphrase.strip():
        raise UnsafeEncryptedBackup('Backup passphrase invalid')
    # Memory-hard derivation; random salt generated afresh per archive.
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        passphrase.encode('utf-8'))


def encrypt_archive(plaintext: bytes, passphrase: str) -> bytes:
    if not isinstance(plaintext, bytes) or not (0 < len(plaintext) <= MAX_PLAINTEXT):
        raise UnsafeEncryptedBackup('Invalid archive size or type')
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    header = MAGIC + salt + nonce
    key = _derive(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + ciphertext


def decrypt_archive(envelope: bytes, passphrase: str) -> bytes:
    minimum = len(MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN
    if (not isinstance(envelope, bytes) or len(envelope) < minimum
            or len(envelope) > MAX_ENCRYPTED or not envelope.startswith(MAGIC)):
        raise UnsafeEncryptedBackup('Invalid encrypted archive')
    i = len(MAGIC)
    salt, nonce = envelope[i:i+SALT_LEN], envelope[i+SALT_LEN:i+SALT_LEN+NONCE_LEN]
    header = envelope[:i+SALT_LEN+NONCE_LEN]
    key = _derive(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, envelope[len(header):], header)
    except (InvalidTag, ValueError):
        # Do not reveal whether the password or the authenticated data is wrong.
        raise UnsafeEncryptedBackup('Encrypted archive authentication failed') from None


def write_private_exclusive(destination, ciphertext: bytes):
    """Atomic exclusive create with 0600 mode, no plaintext disk writes."""
    from pathlib import Path
    path = Path(destination)
    # No writes over preexisting paths, including symbolic links.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
