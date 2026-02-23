import json
with open('C:/temp/missing_analysis.json') as f:
    d = json.load(f)
m = d.get('hash_mismatch', [])
total = sum(x.get('amount', 0) for x in m)
print(f"Missing: {len(m)} items")
print(f"Total amount: ${total:,.2f}")
