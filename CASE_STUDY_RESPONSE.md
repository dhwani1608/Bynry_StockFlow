# StockFlow Case Study Response

## Assumptions

- Each product belongs to exactly one company.
- SKUs are globally unique across the full platform, not just within one company.
- A product may be stocked in many warehouses, so warehouse quantity is modeled separately from the product record.
- Money should be stored with fixed precision using `DECIMAL`, never floating point.
- "Recent sales activity" means at least one sale in the last 30 days.
- `days_until_stockout` is based on the average daily sales for the same product in the same warehouse over the last 30 days.
- Each product has one primary supplier for reordering. If the real product model supports many suppliers, the API can be extended later.
- Low stock threshold is driven by product type, but an inventory-level override could be added later if operations needs warehouse-specific rules.

## Part 1: Code Review and Debugging

### Problems in the original code

1. No request validation
   - The code assumes `request.json` exists and all keys are present.
   - Production impact: malformed requests raise runtime errors, return 500s, and create a poor API experience.

2. No type validation or coercion
   - `price`, `warehouse_id`, and `initial_quantity` are accepted without validation.
   - Production impact: invalid prices, negative quantities, or strings in numeric fields can slip through and corrupt data.

3. Price handling is unsafe
   - The code passes `price` directly without ensuring decimal precision.
   - Production impact: float rounding errors cause billing and reporting issues.

4. SKU uniqueness is not enforced
   - The requirement says SKUs must be unique across the platform, but the endpoint does not check this.
   - Production impact: duplicate SKUs break search, integrations, purchase orders, and barcode workflows.

5. Warehouse ownership is not validated
   - The endpoint trusts `warehouse_id` without checking that the warehouse exists or belongs to the correct company.
   - Production impact: products may be linked to invalid or cross-tenant warehouses, causing serious data integrity issues.

6. Product record incorrectly stores `warehouse_id`
   - Products can exist in multiple warehouses, so warehouse should not live on the product entity itself.
   - Production impact: the model cannot represent the business domain correctly and becomes hard to extend.

7. Two commits for one business operation
   - Product creation and initial inventory creation are committed separately.
   - Production impact: if the second insert fails, the product exists without inventory, leaving partial data and cleanup work.

8. Missing transaction handling
   - There is no rollback on database failure.
   - Production impact: inconsistent state and vague production failures.

9. No protection against duplicate product-inventory rows
   - Inventory is inserted blindly for `(product_id, warehouse_id)`.
   - Production impact: duplicate inventory records can appear unless constrained at the database level.

10. Optional fields are not handled
    - The prompt says some fields may be optional, but the endpoint treats everything except the shown fields as nonexistent.
    - Production impact: the API is brittle and hard to evolve.

11. No inventory movement history
    - The system needs to track inventory changes, but the initial stock creation is not recorded as a movement.
    - Production impact: no audit trail for reconciliation or debugging.

12. Response shape is incomplete
    - The endpoint always returns success without surfacing validation problems or duplicate conflicts with proper status codes.
    - Production impact: client applications cannot react reliably to failures.

### Corrected design

- Validate and normalize request data before writing anything.
- Use `Decimal` for price and validate non-negative quantity.
- Enforce SKU uniqueness with both an application-level check and a database unique constraint.
- Remove `warehouse_id` from the `Product` table.
- Create product, inventory row, and inventory movement in a single transaction.
- Return 201 on success, 400 for bad input, 404 for missing warehouse, and 409 for duplicate SKU.

### Corrected version

```python
@app.route("/api/products", methods=["POST"])
def create_product():
    payload = request.get_json(silent=True) or {}

    required_fields = ["company_id", "name", "sku", "price", "warehouse_id"]
    missing = [field for field in required_fields if payload.get(field) in (None, "")]
    if missing:
        return {"error": f"Missing required fields: {', '.join(missing)}"}, 400

    try:
        price = Decimal(str(payload["price"])).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return {"error": "price must be a valid decimal value"}, 400

    try:
        initial_quantity = int(payload.get("initial_quantity", 0))
    except (TypeError, ValueError):
        return {"error": "initial_quantity must be an integer"}, 400

    if price < 0:
        return {"error": "price cannot be negative"}, 400

    if initial_quantity < 0:
        return {"error": "initial_quantity cannot be negative"}, 400

    warehouse = Warehouse.query.filter_by(
        id=payload["warehouse_id"],
        company_id=payload["company_id"],
    ).first()
    if warehouse is None:
        return {"error": "warehouse not found for company"}, 404

    if Product.query.filter_by(sku=payload["sku"]).first():
        return {"error": "sku already exists"}, 409

    try:
        product = Product(
            company_id=payload["company_id"],
            name=payload["name"].strip(),
            sku=payload["sku"].strip().upper(),
            price=price,
            supplier_id=payload.get("supplier_id"),
            product_type_id=payload.get("product_type_id"),
            description=payload.get("description"),
        )
        db.session.add(product)
        db.session.flush()

        inventory = Inventory(
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity=initial_quantity,
        )
        db.session.add(inventory)

        movement = InventoryMovement(
            product_id=product.id,
            warehouse_id=warehouse.id,
            change_type="initial_stock",
            quantity_delta=initial_quantity,
        )
        db.session.add(movement)

        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"error": "product could not be created because of a data conflict"}, 409
    except Exception:
        db.session.rollback()
        return {"error": "unexpected error while creating product"}, 500

    return {
        "message": "Product created",
        "product_id": product.id,
    }, 201
```

## Part 2: Database Design

### Proposed schema

```sql
CREATE TABLE companies (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE warehouses (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name VARCHAR(255) NOT NULL,
    code VARCHAR(50),
    address_line_1 VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(100),
    postal_code VARCHAR(30),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_warehouses_company_id ON warehouses(company_id);

CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name VARCHAR(255) NOT NULL,
    contact_email VARCHAR(255),
    contact_phone VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_suppliers_company_id ON suppliers(company_id);

CREATE TABLE product_types (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name VARCHAR(100) NOT NULL,
    low_stock_threshold INTEGER NOT NULL CHECK (low_stock_threshold >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, name)
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    product_type_id INTEGER REFERENCES product_types(id),
    primary_supplier_id INTEGER REFERENCES suppliers(id),
    name VARCHAR(255) NOT NULL,
    sku VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    price DECIMAL(12, 2) NOT NULL CHECK (price >= 0),
    is_bundle BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_products_company_id ON products(company_id);
CREATE INDEX idx_products_product_type_id ON products(product_type_id);
CREATE INDEX idx_products_supplier_id ON products(primary_supplier_id);

CREATE TABLE inventory (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, warehouse_id)
);

CREATE INDEX idx_inventory_warehouse_id ON inventory(warehouse_id);
CREATE INDEX idx_inventory_product_id ON inventory(product_id);

CREATE TABLE inventory_movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    change_type VARCHAR(50) NOT NULL,
    quantity_delta INTEGER NOT NULL,
    reference_type VARCHAR(50),
    reference_id INTEGER,
    note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_inventory_movements_product_warehouse_created
    ON inventory_movements(product_id, warehouse_id, created_at DESC);

CREATE TABLE sales_orders (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    ordered_at TIMESTAMP NOT NULL,
    status VARCHAR(50) NOT NULL
);

CREATE INDEX idx_sales_orders_company_ordered_at
    ON sales_orders(company_id, ordered_at DESC);

CREATE TABLE sales_order_lines (
    id INTEGER PRIMARY KEY,
    sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(12, 2) NOT NULL CHECK (unit_price >= 0)
);

CREATE INDEX idx_sales_order_lines_product_id ON sales_order_lines(product_id);

CREATE TABLE bundle_components (
    bundle_product_id INTEGER NOT NULL REFERENCES products(id),
    component_product_id INTEGER NOT NULL REFERENCES products(id),
    component_quantity INTEGER NOT NULL CHECK (component_quantity > 0),
    PRIMARY KEY (bundle_product_id, component_product_id),
    CHECK (bundle_product_id <> component_product_id)
);
```

### Why this schema

- `products` and `warehouses` are separated by `inventory` because products can live in many warehouses.
- `UNIQUE (product_id, warehouse_id)` prevents duplicate inventory rows.
- `inventory_movements` creates an audit trail for adjustments, receiving, sales, and initial stock.
- `product_types.low_stock_threshold` supports the requirement that threshold varies by product type.
- `sales_orders` and `sales_order_lines` support recent-sales filtering and stockout calculations.
- `bundle_components` models bundles without duplicating product records.
- Indexes are added to common join and filter paths: company, product, warehouse, time-window lookups.
- `sku` is globally unique to match the requirement.

### Questions for the product team

1. Is SKU uniqueness truly global, or should it be unique per company?
2. Can a product have multiple suppliers, with one preferred supplier per warehouse or per company?
3. Should low-stock thresholds support overrides at product or warehouse level?
4. What counts as "recent sales activity": 7, 30, or 90 days, and should returns be excluded?
5. Should `days_until_stockout` be calculated per warehouse, company-wide, or from all-channel sales?
6. Can inventory go negative to represent backorders or delayed reconciliation?
7. Do bundles reduce stock only for components, only for the bundle SKU, or both?
8. Do suppliers belong to one company only, or can the same supplier be shared across tenants?
9. Should warehouse transfers create paired inventory movement events?
10. Are inactive products or warehouses excluded from alerts?
11. Do we need soft deletes and full audit history for price and supplier changes?
12. What status values in sales orders should count as true demand for alerting?

## Part 3: API Implementation

### Endpoint

- `GET /api/companies/{company_id}/alerts/low-stock`

### Approach

1. Restrict results to products in warehouses owned by the given company.
2. Join the last 30 days of completed sales to find products with recent activity.
3. Aggregate average daily sales per product and warehouse.
4. Compare current inventory to the threshold from the product type.
5. Return only rows where stock is below threshold.
6. Include supplier details for reordering.

### Edge cases handled

- Company does not exist -> `404`
- Products without recent sales are excluded
- Products without a product type or threshold are excluded from alerts
- Zero sales rate results in `days_until_stockout = null`
- Inventory rows with null or negative data are guarded by schema constraints and validation
- Missing supplier returns `supplier: null`

### Implementation notes

- The runnable reference code lives in [app.py](/c:/Users/DHWANI/Dhwani%20aha/CaseStudy_StockFlow/app.py).
- It includes:
  - SQLAlchemy models for the proposed schema
  - A corrected `POST /api/products`
  - `GET /api/companies/<company_id>/alerts/low-stock`
  - transaction handling and helpful error responses

## Live Discussion Talking Points

- The biggest production risk in Part 1 is partial writes caused by committing the product and inventory separately.
- The main schema design decision is separating `products` from `inventory` to support multi-warehouse storage correctly.
- The main API ambiguity is what "recent sales activity" and "days until stockout" mean operationally, so I made those assumptions explicit instead of hiding them.
