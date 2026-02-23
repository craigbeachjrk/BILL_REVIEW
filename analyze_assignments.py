"""Analyze DynamoDB assignments and show baseline summary"""
import json
from collections import defaultdict

# Load assignments
with open('C:/temp/dynamo_assignments.json', 'r') as f:
    assignments = json.load(f)

# Group by period
by_period = defaultdict(lambda: {'count': 0, 'total': 0})
for a in assignments:
    period = a.get('ubi_period', 'Unknown')
    by_period[period]['count'] += 1
    by_period[period]['total'] += float(a.get('amount', 0))

print('=== OLD PROCESS (DynamoDB) - BASELINE ===')
print('Period          | Count  | Total Amount')
print('-' * 45)
total_count = 0
grand_total = 0
for period in sorted(by_period.keys()):
    data = by_period[period]
    print(f'{period:15} | {data["count"]:6} | ${data["total"]:,.2f}')
    total_count += data['count']
    grand_total += data['total']
print('-' * 45)
print(f'{"TOTAL":15} | {total_count:6} | ${grand_total:,.2f}')

# Save summary for comparison
with open('C:/temp/old_summary.json', 'w') as f:
    json.dump({
        'by_period': {p: {'count': d['count'], 'total': d['total']} for p, d in by_period.items()},
        'total_count': total_count,
        'grand_total': grand_total
    }, f, indent=2)
print('\nBaseline summary saved to C:/temp/old_summary.json')
