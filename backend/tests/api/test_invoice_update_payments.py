"""An invoice edit must not leave a receipt/payment allocation the payment
screens would have refused (#786).

Repro from the issue: a sales invoice to Alpha for Rs 236 (2 x Rs 100 + 18%
GST) has a receipt for Rs 236 allocated to it in full. Editing the invoice
must not be able to:
  1. move it to a different ledger (Beta never paid anything),
  2. shrink its total below the Rs 236 already allocated, or
  3. flip it to a purchase voucher (a sales receipt can't settle a purchase).

An edit that keeps the same ledger and voucher type, and keeps the total at
or above what is allocated, must still go through.
"""

from datetime import datetime


_GST_BY_NAME = {
    "Alpha": "27ABCDE1234F1Z0",
    "Beta": "29ABCDE1234F1ZW",
}


def _create_ledger(client, name):
    response = client.post(
        "/api/ledgers/",
        json={
            "name": name,
            "address": "Mumbai",
            "gst": _GST_BY_NAME.get(name, "27ABCDE1234F1Z0"),
            "phone_number": "9999999999",
            "email": f"{name.lower()}@example.com",
            "website": "",
            "bank_name": "",
            "branch_name": "",
            "account_name": "",
            "account_number": "",
            "ifsc_code": "",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_product(client):
    response = client.post(
        "/api/products/",
        json={
            "sku": "ALLOC-001",
            "name": "Allocation Product",
            "description": "",
            "hsn_sac": "9988",
            "price": 100,
            "gst_rate": 18,
            "unit": "Pieces",
            "allow_decimal": False,
            "maintain_inventory": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _invoice_payload(ledger_id, product_id, voucher_type="sales", quantity=2):
    return {
        "ledger_id": ledger_id,
        "voucher_type": voucher_type,
        "tax_inclusive": False,
        "apply_round_off": False,
        "items": [{"product_id": product_id, "quantity": quantity, "unit_price": 100}],
    }


def _create_invoice(client, ledger_id, product_id, voucher_type="sales", quantity=2):
    response = client.post(
        "/api/invoices/",
        json=_invoice_payload(ledger_id, product_id, voucher_type, quantity),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_receipt(client, ledger_id, amount, invoice_id):
    response = client.post(
        "/api/payments/",
        json={
            "ledger_id": ledger_id,
            "voucher_type": "receipt",
            "amount": amount,
            "date": datetime.utcnow().isoformat(),
            "mode": "bank",
            "invoice_allocations": [
                {"invoice_id": invoice_id, "allocated_amount": amount}
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _setup_fully_paid_invoice(client):
    """Alpha's sales invoice for Rs 236 (2 x Rs 100 + 18% GST), paid in full."""
    alpha_id = _create_ledger(client, "Alpha")
    beta_id = _create_ledger(client, "Beta")
    product_id = _create_product(client)
    invoice = _create_invoice(client, alpha_id, product_id)
    assert invoice["total_amount"] == 236.0
    _create_receipt(client, alpha_id, 236, invoice["id"])
    return alpha_id, beta_id, product_id, invoice


def test_changing_the_ledger_is_refused_when_a_receipt_is_allocated(client):
    alpha_id, beta_id, product_id, invoice = _setup_fully_paid_invoice(client)

    response = client.put(
        f"/api/invoices/{invoice['id']}",
        json=_invoice_payload(beta_id, product_id),
    )

    assert response.status_code == 400, response.text
    assert "receipt" in response.json()["detail"].lower()

    # The invoice still belongs to Alpha.
    after = client.get(f"/api/invoices/{invoice['id']}")
    assert after.status_code == 200, after.text
    assert after.json()["ledger_id"] == alpha_id


def test_shrinking_the_total_below_the_allocated_amount_is_refused(client):
    alpha_id, _beta_id, product_id, invoice = _setup_fully_paid_invoice(client)

    response = client.put(
        f"/api/invoices/{invoice['id']}",
        json=_invoice_payload(alpha_id, product_id, quantity=1),
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "236" in detail

    after = client.get(f"/api/invoices/{invoice['id']}")
    assert after.status_code == 200, after.text
    assert after.json()["total_amount"] == 236.0


def test_changing_voucher_type_is_refused_when_a_receipt_is_allocated(client):
    alpha_id, _beta_id, product_id, invoice = _setup_fully_paid_invoice(client)

    response = client.put(
        f"/api/invoices/{invoice['id']}",
        json=_invoice_payload(alpha_id, product_id, voucher_type="purchase"),
    )

    assert response.status_code == 400, response.text

    after = client.get(f"/api/invoices/{invoice['id']}")
    assert after.status_code == 200, after.text
    assert after.json()["voucher_type"] == "sales"


def test_edit_that_keeps_ledger_and_total_covers_allocation_still_works(client):
    alpha_id, _beta_id, product_id, invoice = _setup_fully_paid_invoice(client)

    # Same ledger, same voucher type, quantity goes up so the total still
    # covers (and now exceeds) the Rs 236 already allocated.
    response = client.put(
        f"/api/invoices/{invoice['id']}",
        json=_invoice_payload(alpha_id, product_id, quantity=3),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == 354.0
