import math

def fibonacci(n):
    """Return a list of n Fibonacci numbers."""
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq

def is_prime(n):
    """Check if a number is prime."""
    if n <= 1:
        return False
    for i in range(2, int(math.sqrt(n)) + 1):
        if n % i == 0:
            return False
    return True

def factorial(n):
    """Calculate factorial of n."""
    if n < 0:
        raise ValueError("Negative input not allowed")
    return 1 if n == 0 else n * factorial(n - 1)

if __name__ == "__main__":
    print("Fibonacci(10):", fibonacci(10))
    print("Is 29 prime?", is_prime(29))
