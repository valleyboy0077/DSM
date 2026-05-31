"""Encryption utilities for iDRAC credentials."""

import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from dsm.config import settings


def _derive_key() -> bytes:
    """Derive AES key from the config encryption key."""
    key_hex = settings.encryption_key
    if len(key_hex) < 32:
        key_hex = key_hex.ljust(32, "0")
    password = bytes.fromhex(key_hex[:32])
    salt = b"dsm-salt-v1"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return kdf.derive(password)


def encrypt_plaintext(plaintext: str) -> str:
    """Encrypt a plaintext string and return hex-encoded ciphertext."""
    key = _derive_key()
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return (iv + ciphertext).hex()


def decrypt_ciphertext(hex_data: str) -> Optional[str]:
    """Decrypt hex-encoded ciphertext back to plaintext."""
    try:
        key = _derive_key()
        data = bytes.fromhex(hex_data)
        iv = data[:16]
        ciphertext = data[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return plaintext.decode()
    except Exception:
        return None
