# Legal / Authorization

Reverse engineering, and especially security testing of a target, is only lawful within an explicit authorization and scope. This is not legal advice; confirm the applicable rules with a qualified professional.

## Authorized writing
- **Get written authorization** from the asset owner before any active testing, disassembly, or PoC execution. A penetration-test contract or a signed statement of work should define the exact systems, IP ranges, and time window.
- For firmware: confirm you may possess and analyze the binary, and that the vendor permits RE (some licenses restrict it).
- Never attack systems you do not own and do not have written permission to test.

## Coordinated Vulnerability Disclosure (CVD)
- If you find a real vulnerability in a product you did not build, do not weaponize it. Report to the vendor through a coordinated disclosure channel (security contact, bug bounty, or a CERT/CSIRT).
- Give the vendor a reasonable embargo window to fix it before any public disclosure. Publish a write-up only after the fix ships and with the vendor's agreement.
- Many jurisdictions give legal protection to researchers who follow CVD rather than weaponizing findings.

## Brazil — Lei 12.737/2012 ("Lei Carolina Dieckmann")
- Criminalizes unauthorized access to another person's device (computers, networks, and — per the 2012 wording — the information therein) without authorization.
- It also criminalizes obtaining or divulging protected information obtained through such unauthorized access.
- Consequences: fines and imprisonment. Any RE work in Brazil must have the owner's authorization or fall squarely under a permitted research carve-out.

## United States — CFAA (18 U.S.C. § 1030)
- The Computer Fraud and Abuse Act criminalizes unauthorized access to computers, and exceeding authorized access.
- Even where a device is "publicly reachable," exceeding the granted access (or accessing without authorization) is a federal crime.
- CFAA enforcement has narrowed around authorization boundaries in recent case law, but the rule remains: stay inside written authorization.

## Practical guardrails
- Keep all testing on systems you own or are contractually authorized to test.
- Do not exfiltrate or publish user data, credentials, or proprietary source beyond what the analysis requires.
- Sanitize reports to neutral placeholders; never include real internal identifiers or third-party secrets.
