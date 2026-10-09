import csv
import sys

from db import connect

if __name__ == "__main__":
    writer = csv.writer(sys.stdout)
    for row in connect().execute("SELECT id, customer, item, status FROM orders"):
        writer.writerow(tuple(row))
