from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import ceil
import os
from typing import Any

from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, func
from sqlalchemy.exc import IntegrityError


db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class Company(TimestampMixin, db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)


class Warehouse(TimestampMixin, db.Model):
    __tablename__ = "warehouses"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    company = db.relationship("Company", backref="warehouses")


class Supplier(TimestampMixin, db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    contact_email = db.Column(db.String(255))
    contact_phone = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    company = db.relationship("Company", backref="suppliers")


class ProductType(TimestampMixin, db.Model):
    __tablename__ = "product_types"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_product_types_company_name"),
        CheckConstraint("low_stock_threshold >= 0", name="ck_product_types_threshold_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    low_stock_threshold = db.Column(db.Integer, nullable=False)

    company = db.relationship("Company", backref="product_types")


class Product(TimestampMixin, db.Model):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    product_type_id = db.Column(db.Integer, db.ForeignKey("product_types.id"), index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), index=True)
    name = db.Column(db.String(255), nullable=False)
    sku = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(12, 2), nullable=False)
    is_bundle = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    company = db.relationship("Company", backref="products")
    product_type = db.relationship("ProductType", backref="products")
    supplier = db.relationship("Supplier", backref="products")


class Inventory(TimestampMixin, db.Model):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_inventory_product_warehouse"),
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)

    product = db.relationship("Product", backref="inventory_records")
    warehouse = db.relationship("Warehouse", backref="inventory_records")


class InventoryMovement(db.Model):
    __tablename__ = "inventory_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id"), nullable=False, index=True)
    change_type = db.Column(db.String(50), nullable=False)
    quantity_delta = db.Column(db.Integer, nullable=False)
    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.Integer)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class SalesOrder(TimestampMixin, db.Model):
    __tablename__ = "sales_orders"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id"), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False)
    ordered_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    company = db.relationship("Company", backref="sales_orders")
    warehouse = db.relationship("Warehouse", backref="sales_orders")


class SalesOrderLine(db.Model):
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_order_lines_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sales_order_lines_price_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sales_order_id = db.Column(db.Integer, db.ForeignKey("sales_orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)

    sales_order = db.relationship("SalesOrder", backref="lines")
    product = db.relationship("Product", backref="sales_lines")


class BundleComponent(db.Model):
    __tablename__ = "bundle_components"
    __table_args__ = (
        CheckConstraint("component_quantity > 0", name="ck_bundle_components_qty_positive"),
        CheckConstraint("bundle_product_id <> component_product_id", name="ck_bundle_components_not_self"),
    )

    bundle_product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        primary_key=True,
    )
    component_product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        primary_key=True,
    )
    component_quantity = db.Column(db.Integer, nullable=False)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///stockflow.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    register_routes(app)

    with app.app_context():
        db.create_all()

    return app


def parse_decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError(f"{field_name} must be a valid decimal value")


def parse_int(value: Any, field_name: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer")

    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")

    return parsed


def register_routes(app: Flask) -> None:
    @app.route("/api/dev/seed", methods=["POST"])
    def seed_data():
        existing_company = Company.query.filter_by(name="Demo Company").first()
        if existing_company is not None:
            warehouse = Warehouse.query.filter_by(company_id=existing_company.id, code="MAIN").first()
            return jsonify(
                {
                    "message": "Seed data already exists",
                    "company_id": existing_company.id,
                    "warehouse_id": warehouse.id if warehouse else None,
                }
            ), 200

        try:
            company = Company(name="Demo Company")
            db.session.add(company)
            db.session.flush()

            warehouse = Warehouse(
                company_id=company.id,
                name="Main Warehouse",
                code="MAIN",
                is_active=True,
            )
            db.session.add(warehouse)
            db.session.flush()

            supplier = Supplier(
                company_id=company.id,
                name="Supplier Corp",
                contact_email="orders@supplier.com",
                contact_phone="+1-555-0100",
                is_active=True,
            )
            db.session.add(supplier)
            db.session.flush()

            product_type = ProductType(
                company_id=company.id,
                name="Standard",
                low_stock_threshold=20,
            )
            db.session.add(product_type)
            db.session.flush()

            product = Product()
            product.company_id = company.id
            product.product_type_id = product_type.id
            product.supplier_id = supplier.id
            product.name = "Widget A"
            product.sku = "WID-001"
            product.description = "Seeded sample product"
            product.price = Decimal("19.99")
            db.session.add(product)
            db.session.flush()

            inventory = Inventory()
            inventory.product_id = product.id
            inventory.warehouse_id = warehouse.id
            inventory.quantity = 5
            db.session.add(inventory)

            movement = InventoryMovement()
            movement.product_id = product.id
            movement.warehouse_id = warehouse.id
            movement.change_type = "initial_stock"
            movement.quantity_delta = 5
            movement.note = "Seeded inventory"
            db.session.add(movement)

            sales_order = SalesOrder(
                company_id=company.id,
                warehouse_id=warehouse.id,
                status="completed",
                ordered_at=utcnow() - timedelta(days=10),
            )
            db.session.add(sales_order)
            db.session.flush()

            sales_line = SalesOrderLine(
                sales_order_id=sales_order.id,
                product_id=product.id,
                quantity=12,
                unit_price=Decimal("19.99"),
            )
            db.session.add(sales_line)

            db.session.commit()
        except Exception:
            db.session.rollback()
            return jsonify({"error": "failed to seed demo data"}), 500

        return jsonify(
            {
                "message": "Seed data created",
                "company_id": company.id,
                "warehouse_id": warehouse.id,
                "product_type_id": product_type.id,
                "supplier_id": supplier.id,
                "product_id": product.id,
            }
        ), 201

    @app.route("/api/products", methods=["POST"])
    def create_product():
        payload = request.get_json(silent=True) or {}

        required_fields = ["company_id", "name", "sku", "price", "warehouse_id"]
        missing = [field for field in required_fields if payload.get(field) in (None, "")]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        try:
            company_id = parse_int(payload["company_id"], "company_id", minimum=1)
            warehouse_id = parse_int(payload["warehouse_id"], "warehouse_id", minimum=1)
            initial_quantity = parse_int(payload.get("initial_quantity", 0), "initial_quantity", minimum=0)
            price = parse_decimal(payload["price"], "price")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if price < 0:
            return jsonify({"error": "price cannot be negative"}), 400

        sku = str(payload["sku"]).strip().upper()
        name = str(payload["name"]).strip()
        if not sku or not name:
            return jsonify({"error": "name and sku must be non-empty strings"}), 400

        warehouse = Warehouse.query.filter_by(id=warehouse_id, company_id=company_id, is_active=True).first()
        if warehouse is None:
            return jsonify({"error": "warehouse not found for company"}), 404

        if Product.query.filter_by(sku=sku).first():
            return jsonify({"error": "sku already exists"}), 409

        supplier_id = payload.get("supplier_id")
        product_type_id = payload.get("product_type_id")

        if supplier_id is not None:
            try:
                supplier_id = parse_int(supplier_id, "supplier_id", minimum=1)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

            supplier = Supplier.query.filter_by(id=supplier_id, company_id=company_id, is_active=True).first()
            if supplier is None:
                return jsonify({"error": "supplier not found for company"}), 404

        if product_type_id is not None:
            try:
                product_type_id = parse_int(product_type_id, "product_type_id", minimum=1)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

            product_type = ProductType.query.filter_by(id=product_type_id, company_id=company_id).first()
            if product_type is None:
                return jsonify({"error": "product_type not found for company"}), 404

        try:
            # One transaction keeps product, stock, and audit history consistent.
            product = Product()
            product.company_id = company_id
            product.product_type_id = product_type_id
            product.supplier_id = supplier_id
            product.name = name
            product.sku = sku
            product.description = payload.get("description")
            product.price = price
            db.session.add(product)
            db.session.flush()

            inventory = Inventory()
            inventory.product_id = product.id
            inventory.warehouse_id = warehouse_id
            inventory.quantity = initial_quantity
            db.session.add(inventory)

            movement = InventoryMovement()
            movement.product_id = product.id
            movement.warehouse_id = warehouse_id
            movement.change_type = "initial_stock"
            movement.quantity_delta = initial_quantity
            movement.note = "Inventory created during product setup"
            db.session.add(movement)

            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "product could not be created because of a data conflict"}), 409
        except Exception:
            db.session.rollback()
            return jsonify({"error": "unexpected error while creating product"}), 500

        return jsonify({"message": "Product created", "product_id": product.id}), 201

    @app.route("/api/companies/<int:company_id>/alerts/low-stock", methods=["GET"])
    def get_low_stock_alerts(company_id: int):
        company = Company.query.get(company_id)
        if company is None:
            return jsonify({"error": "company not found"}), 404

        recent_days = request.args.get("recent_days", default=30, type=int)
        if recent_days is None or recent_days <= 0:
            return jsonify({"error": "recent_days must be a positive integer"}), 400

        cutoff = utcnow() - timedelta(days=recent_days)

        sales_subquery = (
            db.session.query(
                SalesOrderLine.product_id.label("product_id"),
                SalesOrder.warehouse_id.label("warehouse_id"),
                func.sum(SalesOrderLine.quantity).label("units_sold"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(
                SalesOrder.company_id == company_id,
                SalesOrder.status == "completed",
                SalesOrder.ordered_at >= cutoff,
            )
            .group_by(SalesOrderLine.product_id, SalesOrder.warehouse_id)
            .subquery()
        )

        rows = (
            db.session.query(
                Product.id.label("product_id"),
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                Warehouse.id.label("warehouse_id"),
                Warehouse.name.label("warehouse_name"),
                Inventory.quantity.label("current_stock"),
                ProductType.low_stock_threshold.label("threshold"),
                Supplier.id.label("supplier_id"),
                Supplier.name.label("supplier_name"),
                Supplier.contact_email.label("supplier_contact_email"),
                sales_subquery.c.units_sold.label("units_sold"),
            )
            .join(Inventory, Inventory.product_id == Product.id)
            .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
            .join(ProductType, ProductType.id == Product.product_type_id)
            .outerjoin(Supplier, Supplier.id == Product.supplier_id)
            .join(
                sales_subquery,
                (sales_subquery.c.product_id == Product.id)
                & (sales_subquery.c.warehouse_id == Warehouse.id),
            )
            .filter(
                Product.company_id == company_id,
                Warehouse.company_id == company_id,
                Product.is_active.is_(True),
                Warehouse.is_active.is_(True),
                Inventory.quantity < ProductType.low_stock_threshold,
            )
            .order_by(
                (ProductType.low_stock_threshold - Inventory.quantity).desc(),
                Product.name.asc(),
            )
            .all()
        )

        alerts = []
        for row in rows:
            units_sold = int(row.units_sold or 0)
            avg_daily_sales = units_sold / recent_days if units_sold > 0 else 0
            days_until_stockout = None
            if avg_daily_sales > 0:
                days_until_stockout = ceil(row.current_stock / avg_daily_sales)

            alerts.append(
                {
                    "product_id": row.product_id,
                    "product_name": row.product_name,
                    "sku": row.sku,
                    "warehouse_id": row.warehouse_id,
                    "warehouse_name": row.warehouse_name,
                    "current_stock": row.current_stock,
                    "threshold": row.threshold,
                    "days_until_stockout": days_until_stockout,
                    "supplier": (
                        {
                            "id": row.supplier_id,
                            "name": row.supplier_name,
                            "contact_email": row.supplier_contact_email,
                        }
                        if row.supplier_id is not None
                        else None
                    ),
                }
            )

        return jsonify({"alerts": alerts, "total_alerts": len(alerts)})


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
