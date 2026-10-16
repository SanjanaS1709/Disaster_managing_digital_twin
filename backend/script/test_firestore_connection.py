
"""Backward-compatible entry point for safe live Firestore verification.

The verifier creates and removes a unique temporary collection and never prints
credential values or writes to a fixed shared document.
"""
from verify_live_firestore import main


if __name__ == "__main__":
    main()
