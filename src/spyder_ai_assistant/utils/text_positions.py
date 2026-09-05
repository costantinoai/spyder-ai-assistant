"""Conversions between Qt UTF-16 positions and Python string indexes."""


def utf16_length(text):
    """Return the number of QTextCursor position units in text."""
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


def python_index(text, qt_position):
    """Translate a UTF-16 cursor offset to a Python string index."""
    prefix = text.encode('utf-16-le', errors='surrogatepass')[:qt_position * 2]
    return len(prefix.decode('utf-16-le', errors='surrogatepass'))
