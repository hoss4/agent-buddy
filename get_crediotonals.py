from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/calendar']

flow = InstalledAppFlow.from_client_secrets_file(
    '/home/hossam/Downloads/google_auth.json',
    scopes=SCOPES
)

# Opens browser for you to authorize
creds = flow.run_local_server(port=0)

print("GOOGLE_REFRESH_TOKEN =", creds.refresh_token)
print("GOOGLE_CLIENT_ID     =", creds.client_id)
print("GOOGLE_CLIENT_SECRET =", creds.client_secret)
