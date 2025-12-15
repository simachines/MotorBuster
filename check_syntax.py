try:
    import native_app
    print("Import successful")
except ImportError as e:
    print(f"Import Error: {e}")
except SyntaxError as e:
    print(f"Syntax Error: {e}")
except Exception as e:
    print(f"Runtime Error (expected if headless): {e}")
