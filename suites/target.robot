*** Settings ***
Library    cloudshield.robot_keywords.TargetChecks    ${BASE_URL}    ${CREDENTIALS_FILE}

*** Variables ***
${BASE_URL}            http://127.0.0.1:18000
${CREDENTIALS_FILE}    ../.env.phase1.local

*** Test Cases ***
api-health
    [Tags]    smoke
    Health Should Respond

anonymous-user-denied
    [Tags]    auth
    Anonymous User Lookup Should Be Denied

database-backed-user-lookup
    [Tags]    auth    database
    Seeded Admin Lookup Should Succeed
