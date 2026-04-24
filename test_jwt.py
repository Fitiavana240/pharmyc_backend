from jose import jwt
data = {"sub": "alex"}
token = jwt.encode(data, "secret", algorithm="HS256")
print("Token generé:", token)
