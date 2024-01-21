import binascii, os, math, json, hashlib

bytes_from_int = chr if bytes == str else lambda x: bytes([x])

def b2h(b):
    return binascii.hexlify(b).decode('utf-8')

def random_hex(length):
    return binascii.b2a_hex(os.urandom(length))

def double_hash(b): 
	return hashlib.sha256(hashlib.sha256(b).digest()).digest()

def inverse_hash(hashstring):
	hashstring = hashstring[::-1]
	return ''.join([hashstring[i:i+2][::-1] for i in range(0, len(hashstring), 2)])

def ib2h(b):
	return inverse_hash(b2h(b))

class JsonDecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o,  decimal.Decimal):
            return str(o)
        return super(DecimalEncoder, self).default(o)

def decode_value(key, value):
    #Repeated key to make both same length
    adjusted_key = key* int(math.ceil(float(len(value))/len(key)))
    adjusted_key = adjusted_key[:len(value)]
    return bytes([_a ^ _b for _a, _b in zip(adjusted_key, value)])
