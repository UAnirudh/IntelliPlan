"""
studvue.py — Quick local test script for the StudentVue SOAP API.
Credentials are loaded from environment variables, never hardcoded.

Usage:
  SV_USER=yourUsername SV_PASS=yourPassword SV_URL=https://wa-nor-psv.edupoint.com python studvue.py
"""

import os
import requests

url = os.getenv("SV_URL", "https://wa-nor-psv.edupoint.com") + "/Service/PXPCommunication.asmx"
username = os.getenv("SV_USER")
password = os.getenv("SV_PASS")

if not username or not password:
    print("ERROR: Set SV_USER and SV_PASS environment variables before running.")
    exit(1)

headers = {
    "Content-Type": "text/xml; charset=utf-8",
    "SOAPAction": "http://edupoint.com/webservices/ProcessWebServiceRequest"
}
body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ProcessWebServiceRequest xmlns="http://edupoint.com/webservices/">
      <userID>{username}</userID>
      <password>{password}</password>
      <skipLoginLog>1</skipLoginLog>
      <parent>0</parent>
      <webServiceHandleName>PXPWebServices</webServiceHandleName>
      <methodName>Gradebook</methodName>
      <paramStr>&lt;Parms/&gt;</paramStr>
    </ProcessWebServiceRequest>
  </soap:Body>
</soap:Envelope>"""

response = requests.post(url, headers=headers, data=body, timeout=10)
print(response.status_code)
print(response.text[:500])