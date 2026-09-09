"""
Generates (once) and loads a self-signed TLS certificate, so the app
can serve HTTPS/WSS without depending on any external certificate
authority or hosted service - required because browsers only allow
camera/microphone access (getUserMedia) on a "secure context" (HTTPS
or localhost), and guests reach this app over plain IP addresses, not
localhost. `cryptography` is already an aiortc dependency, not a new
one introduced for this.

STATUS: first draft, not yet run. Guests WILL see a "connection isn't
private" browser warning and need to click through it once, since this
cert isn't signed by a trusted authority - expected tradeoff for
staying self-hosted, not a bug to fix.
"""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = os.path.join(os.path.dirname(__file__), "certs")
CERT_PATH = os.path.join(CERT_DIR, "cert.pem")
KEY_PATH = os.path.join(CERT_DIR, "key.pem")


def ensure_certificate() -> tuple[str, str]:
    """Returns (cert_path, key_path), generating a new self-signed
    certificate on first run if one doesn't already exist, so the same
    cert persists across restarts instead of guests re-accepting a new
    one every session."""
    if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
        return CERT_PATH, KEY_PATH

    os.makedirs(CERT_DIR, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ReSync Live (self-signed)")])

    # Browsers will still show a warning regardless of SAN contents,
    # since this isn't signed by a trusted CA - the host's actual public
    # IP changes session to session anyway (see docs/ARCHITECTURE.md,
    # manual IP sharing), so there's no fixed address to put here that
    # would avoid the warning. Guests click "Advanced -> Proceed" once.
    san_list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(KEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    return CERT_PATH, KEY_PATH
