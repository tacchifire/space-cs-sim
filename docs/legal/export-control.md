# Export control

*日本語版: [export-control.ja.md](export-control.ja.md)*

**Status: UNVERIFIED. No classification has been made, and none is claimed.**

This page records what is known, what is not, and who has to decide. It is not legal advice and it
is not a determination.

## What this project contains

Working attack tooling against subsets of CCSDS and ECSS PUS protocols and the CubeSat Space
Protocol, targeting a synthetic spacecraft that runs entirely inside an emulator. Everything is
published openly and developed in public. Section 6.5 of the design plans an AES-256-GCM
implementation of CCSDS SDLS; it is not written yet, and when it is it will need its own look.

## What has been read, and what it appears to say

Recorded so the next person does not start from nothing. None of this is a conclusion.

**ITAR.** Being space-themed does not by itself make a repository ITAR-controlled. Technical data
must relate to a defense article, and the definitions carve out information that is published and
generally accessible to the public.
- 22 CFR 120.33 — https://www.ecfr.gov/current/title-22/chapter-I/subchapter-M/part-120/subpart-C/section-120.33
- 22 CFR 120.34 — https://www.ecfr.gov/current/title-22/chapter-I/subchapter-M/part-120/subpart-C/section-120.34

**EAR.** BIS guidance on cybersecurity items states that publicly available intrusion-related
software and technology is generally not subject to the EAR, and that a machine-executable exploit
is not ECCN 4D004 by that fact alone. The same guidance flags military offensive cyber tooling,
command-and-control behaviour, end use, and encryption as separate questions.
- https://media.bis.gov/media/documents/cybersecurity-items-ear-faqs-revised-feb-2022-final.pdf

**Encryption.** Incorporating open-source cryptography does not settle classification on its own.
- https://www.bis.gov/learn-support/encryption-controls/encryption-items-not-subject-to-ear

## What must not be written anywhere

Until a qualified export specialist or counsel records a rationale, this project must not state or
imply "EAR99", "not ITAR", "no licence required", or any equivalent. Absence of a determination is
not a determination.

## Re-review triggers

Any of these means this page is out of date and someone has to look again:

- cryptography is added or changed — SDLS, key management, anything using the STM32 CRYP peripheral
- anything resembling command-and-control, remote tasking, or a persistence mechanism is added
- material specific to a military system, or derived from a controlled source, enters the tree
- the project is used in paid or contracted work, or distributed other than as a public repository
- a contributor or user is in a jurisdiction with its own rules on intrusion software

## Owner

Unassigned. This is a gap, and it is a release blocker for any use beyond a public repository of
open research. Whoever takes it should record the date, the scope reviewed, and the rationale here.
