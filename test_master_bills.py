import boto3
import json

# Initialize DynamoDB client
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
ddb = session.client('dynamodb')

# Get master bills config
resp = ddb.get_item(
    TableName='jrk-bill-config',
    Key={
        "PK": {"S": "CONFIG#master-bills-latest"},
        "SK": {"S": "v1"}
    }
)

if "Item" in resp:
    item = resp["Item"]
    data_str = item.get("Data", {}).get("S")
    if data_str:
        parsed = json.loads(data_str)
        print(f"Found {len(parsed)} master bills")
        for mb in parsed:
            print(f"  ID: {repr(mb.get('master_bill_id'))}")
            print(f"  Property: {mb.get('property_name')}")
            print(f"  Charge Code: {mb.get('ar_code_mapping')}")
            print(f"  Amount: ${mb.get('utility_amount')}")
            print()

        # Test the lookup
        test_id = "100056623|GASIN - GAS INCOME|Gas|12/2025|12/2025"
        print(f"Looking for ID: {repr(test_id)}")
        found = False
        for mb in parsed:
            if mb.get("master_bill_id") == test_id:
                print("FOUND MATCH!")
                found = True
                break
        if not found:
            print("NO MATCH FOUND")
    else:
        print("No Data field")
else:
    print("No Item found")
