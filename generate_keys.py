"""
Run this once to generate the RSA key pair needed for LTI 1.3.
Then paste the private key into lti_config.json.
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()
).decode("utf-8")

public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode("utf-8")

with open("lti_private.pem", "w") as f:
    f.write(private_pem)

with open("lti_public.pem", "w") as f:
    f.write(public_pem)

print("Keys saved to lti_private.pem and lti_public.pem")
print()
print("Paste the contents of lti_private.pem into lti_config.json as the 'rsa_key' value.")
print("Keep lti_private.pem secret — do NOT commit it to git.")
