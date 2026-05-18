from secrets import choice
from string import ascii_letters
def crypt(length: int):
    return ''.join(choice(ascii_letters) for _ in range(length))