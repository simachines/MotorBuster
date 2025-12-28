# How to Switch Between Software and Hardware Sine

## Quick Toggle

Open the Python console (during development) and run:

```python
# Switch to hardware SINE (will fail on Simagic, but try on other devices)
engine.use_software_sine = False

# Switch back to software SINE (default, works on Simagic)
engine.use_software_sine = True
```

## Current Status

Check which mode you're using:
```python
print(f"Using Software Sine: {engine.use_software_sine}")
```

## Effect

- **Software Sine (True)**: Uses rapid constant force updates at 60 Hz to simulate sine waves. Works on Simagic.
- **Hardware Sine (False)**: Attempts to use native SDL SINE effects. Fails on Simagic but may work on other wheelbases.

## Default

The default is `use_software_sine = True` (Software mode) because it works reliably on Simagic wheelbases.
