from py_vapid import Vapid
v = Vapid()
v.generate_keys()
import base64
pub = v.public_key.public_bytes(
    __import__('cryptography').hazmat.primitives.serialization.Encoding.X962,
    __import__('cryptography').hazmat.primitives.serialization.PublicFormat.UncompressedPoint
)
priv = v.private_key.private_bytes(
    __import__('cryptography').hazmat.primitives.serialization.Encoding.PEM,
    __import__('cryptography').hazmat.primitives.serialization.PrivateFormat.PKCS8,
    __import__('cryptography').hazmat.primitives.serialization.NoEncryption()
)
print('PUBLIC:', base64.urlsafe_b64encode(pub).decode().rstrip('='))
print('PRIVATE (keep secret):')
print(priv.decode())