# v1.7 Deployment Checklist

## Prerequisites
- [ ] Attorney review of v1.7 methodology complete
- [ ] Attorney sign-off on v1.7 API Terms of Service
- [ ] migration_v1_7.py reviewed and tested
- [ ] migration_claims_methodology_version.sql reviewed

## Database changes
- [ ] Run scripts/migration_claims_methodology_version.sql
- [ ] Run scripts/migration_v1_7.py
- [ ] UPDATE claims SET methodology_version = 'v1.6' WHERE methodology_version IS NULL
- [ ] Verify all existing claims tagged correctly

## Pipeline changes
- [ ] Update verdict_engine.py to write methodology_version='v1.7' on new evaluations
- [ ] Update extract_claims.py if v1.7 changes extraction
- [ ] Deploy verdict pipeline changes
- [ ] Verify new claims arriving with v1.7 tag

## API changes
- [ ] Update PUBLIC_METHODOLOGY_VERSIONS = ['v1.6', 'v1.7'] in railway_api_refresh.py
- [ ] Deploy refresh service
- [ ] Verify api_claims, api_outlets, api_debate_claims show mix of v1.6 and v1.7
- [ ] Update OpenAPI spec to mention v1.7
- [ ] Deploy verisreports

## Public surface changes
- [ ] Update methodology page (static/methodology/data.js) with v1.7 content
- [ ] Update landing page hero eyebrow: "Methodology v1.7 · 160+ outlets tracked"
- [ ] Update landing page methodology stamp pill to v1.7
- [ ] Update landing page example claims to show methodology_version: "v1.7"
- [ ] Update landing page FAQ if v1.7 adds new verdict types or claim origins
- [ ] Add FAQ entry: "What is the challenge process?"
- [ ] Add provisional/final verdict field to debate claim examples
- [ ] Publish v1.7 API Terms of Service at /terms

## Verification
- [ ] API returns mixed v1.6 and v1.7 records as expected
- [ ] No customer-facing surface still shows v1.6 as current version
- [ ] OpenAPI spec validates against new response shapes
- [ ] All FAQ surfaces consistent (methodology, OpenAPI, landing page)

## Announcement
- [ ] Email design partners with v1.7 release notes
- [ ] Document in next session brief
