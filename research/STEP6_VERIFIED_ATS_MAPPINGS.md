# Step 6 — Verified ATS mappings and public listing entry points

Applied on 2026-08-11.

## Explicit provider mappings

### Greenhouse
- Stripe (`stripe`)
- Databricks (`databricks`)
- PhonePe (`phonepe`)
- MongoDB (`mongodb`)
- Rubrik (`rubrik`)
- Groww (`groww`)

### Workday
- Salesforce — `salesforce.wd12.myworkdayjobs.com`, tenant `salesforce`, site `External_Career_Site`
- Walmart Global Tech — `walmart.wd504.myworkdayjobs.com`, tenant `walmart`, site `WalmartExternal`
- Adobe — `adobe.wd5.myworkdayjobs.com`, tenant `adobe`, site `external_experienced`
- PayPal — `paypal.wd1.myworkdayjobs.com`, tenant `paypal`, site `jobs`
- Cohesity — `cohesity.wd5.myworkdayjobs.com`, tenant `cohesity`, site `Cohesity_Careers`

### Eightfold
- Morgan Stanley — `morganstanley.eightfold.ai`

### SmartRecruiters
- ServiceNow — company identifier `ServiceNow`

## Public listing entry-point improvements
- Uber → `https://jobs.uber.com/en/jobs/`
- Intuit → India jobs listing
- Meesho → engineering jobs listing

These changes reduce dependence on generic/browser discovery for companies whose provider is already known.
