FLIPKART-INSPIRED E-COMMERCE DEMAND-TO-SUPPLY IMPACT AGENT
SYNTHETIC DATASET

PURPOSE
Prototype an AI agent that evaluates whether an e-commerce fulfilment network can handle a demand shock (e.g., a BBD-style sale), identifies inventory/supplier/fulfilment bottlenecks, and recommends actions.

IMPORTANT
- This is 100% synthetic data.
- It is inspired by publicly described e-commerce/Flipkart operating concepts only.
- It is NOT Flipkart internal data and must not be presented as such.

DATASET SIZE
- 30 SKUs
- 4 synthetic fulfilment centres
- 8 synthetic suppliers
- 60 days of historical orders
- 120 FC-SKU inventory records
- 150 purchase orders, filtered to the 30-SKU scope
- 5 campaign/category scenarios

CORE FLOW
Demand -> Inventory -> Supplier Replenishment -> Fulfilment Capacity -> SLA Risk -> Bottleneck -> Recommendation

SUGGESTED USER QUESTIONS FOR THE AGENT
1. Can the network support a 20% demand increase?
2. Which fulfilment centres become capacity constrained at 30% demand uplift?
3. Which SKU-FC combinations face inventory coverage risk?
4. Which suppliers could become replenishment bottlenecks?
5. What should the operations manager do before the campaign?

FILES
Products.csv
Historical_Orders.csv
Current_Inventory.csv
Suppliers.csv
Purchase_Orders.csv
Fulfilment_Capacity.csv
Campaign_Scenarios.csv
