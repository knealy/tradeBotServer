import requests

url = "https://api.topstepx.com/api/Contract/list"
headers = {
    "Authorization": "Bearer OGb7bgF05DjaLqRTkZgpP1zmmPE20Vr5gCZay9TpCR0=",
    "Content-Type": "application/json",
}

resp = requests.get(url, headers=headers)

print("Status code:", resp.status_code)
print("Headers:", resp.headers)
print("Raw text:", repr(resp.text))
