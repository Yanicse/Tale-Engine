"""Small helpers for the sentences the program writes."""


def counted(number, noun, plural=None):
    """`1 batch`, `2 batches`: the noun agrees with the number."""
    return f"{number} {noun if number == 1 else plural or noun + 's'}"
