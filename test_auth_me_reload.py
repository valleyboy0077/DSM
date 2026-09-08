#!/usr/bin/env python3
"""Regression test: a valid DSM token must survive SPA reload via /auth/me."""
import httpx

BASE = "http://127.0.0.1:8080"
client = httpx.Client(base_url=BASE, timeout=10)

login = client.post('/auth/login', json={'username': 'admin', 'password': 'admin'})
assert login.status_code == 200, f'login failed: {login.status_code} {login.text}'
token = login.json()['access_token']

me = client.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
assert me.status_code == 200, f'/auth/me failed: {me.status_code} {me.text}'
data = me.json()
assert data['username'] == 'admin'
assert data['is_superuser'] is True
assert 'roles' in data and isinstance(data['roles'], list)

print('/auth/me reload regression test passed')
