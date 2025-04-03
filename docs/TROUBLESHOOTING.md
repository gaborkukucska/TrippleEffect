# Troubleshooting Guide

## Common Issues

### 1. Termux Storage Permissions
```bash
# Reset permissions
termux-setup-storage
chmod 700 ~/TrippleEffect
```

### 2. Venv Creation Failures
```bash
# Manual cleanup
rm -rf ~/TrippleEffect/sandbox/*
pkg install python
python -m ensurepip
```

### 3. API Connection Errors
1. Verify API keys in agent configs
2. Test connectivity:
```python
curl api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"
```

## Error Code Reference
| Code  | Solution |
|-------|----------|
| 507   | Clean sandbox: `python manage.py clean-sandbox` |
| 429   | Reduce agent polling frequency |
| 403   | Rotate API keys |
