"""Creates sales.db: a small e-commerce database for the agent to query."""
import random
import sqlite3
from datetime import date, timedelta

# Fixed seed = same "random" data every time you run it
random.seed(42)

# Connect to the database file (creates it if it doesn't exist)
conn = sqlite3.connect("sales.db")
cur = conn.cursor()

# ---------- 1. Create the tables ----------
cur.executescript("""
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT,
    city        TEXT,
    signup_date DATE
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name       TEXT,
    category   TEXT,
    price      REAL
);

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers,
    order_date  DATE,
    status      TEXT
);

CREATE TABLE order_items (
    item_id    INTEGER PRIMARY KEY,
    order_id   INTEGER REFERENCES orders,
    product_id INTEGER REFERENCES products,
    quantity   INTEGER
);
""")

# ---------- 2. Add 200 customers ----------
cities = ["Hyderabad", "Bengaluru", "Mumbai", "Delhi", "Chennai", "Pune", "Kolkata"]
first_names = ["Aarav", "Diya", "Rohan", "Ananya", "Vikram", "Sneha", "Karthik", "Priya", "Arjun", "Meera"]
last_names = ["Reddy", "Sharma", "Iyer", "Patel", "Rao", "Gupta", "Nair", "Singh"]

for i in range(1, 201):
    name = f"{random.choice(first_names)} {random.choice(last_names)}"
    city = random.choice(cities)
    signup = (date(2024, 1, 1) + timedelta(days=random.randint(0, 500))).isoformat()
    cur.execute("INSERT INTO customers VALUES (?, ?, ?, ?)", (i, name, city, signup))

# ---------- 3. Add 10 products ----------
products = [
    ("Wireless Earbuds", "Electronics", 2499),
    ("Smartwatch", "Electronics", 5999),
    ("Power Bank", "Electronics", 1299),
    ("Laptop Stand", "Accessories", 899),
    ("Mechanical Keyboard", "Accessories", 3499),
    ("Backpack", "Fashion", 1799),
    ("Running Shoes", "Fashion", 3999),
    ("Cotton T-Shirt", "Fashion", 499),
    ("Coffee Maker", "Home", 4499),
    ("Desk Lamp", "Home", 1199),
]
for i, (name, category, price) in enumerate(products, start=1):
    cur.execute("INSERT INTO products VALUES (?, ?, ?, ?)", (i, name, category, price))

# ---------- 4. Add 1500 orders, each with 1-3 products ----------
item_id = 1
for order_id in range(1, 1501):
    order_date = (date(2025, 1, 1) + timedelta(days=random.randint(0, 546))).isoformat()
    status = random.choices(
        ["delivered", "shipped", "cancelled", "returned"],
        weights=[75, 10, 10, 5],
    )[0]
    customer_id = random.randint(1, 200)
    cur.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                (order_id, customer_id, order_date, status))

    for product_id in random.sample(range(1, 11), random.randint(1, 3)):
        quantity = random.randint(1, 3)
        cur.execute("INSERT INTO order_items VALUES (?, ?, ?, ?)",
                    (item_id, order_id, product_id, quantity))
        item_id += 1

# ---------- 5. Save and close ----------
conn.commit()
conn.close()
print("Created sales.db: 200 customers, 10 products, 1500 orders")