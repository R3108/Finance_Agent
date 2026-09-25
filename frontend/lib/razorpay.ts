"use client";

import type { CheckoutSession } from "@/lib/types";

/** What Razorpay Checkout hands back after a successful payment (subscription id OR order id, per mode). */
export type RazorpaySuccess = {
  razorpay_payment_id: string; razorpay_signature: string; razorpay_subscription_id?: string; razorpay_order_id?: string;
};

type RazorpayInstance = { open: () => void; on: (event: "payment.failed", cb: (r: { error: { description: string } }) => void) => void };
declare global {
  interface Window { Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance }
}

const SCRIPT = "https://checkout.razorpay.com/v1/checkout.js";
let loading: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; reject(new Error("Couldn't load Razorpay Checkout. Check your connection or ad blocker.")); };
    document.body.appendChild(s);
  });
  return loading;
}

export type CheckoutResult = { paid: RazorpaySuccess } | { closed: true; lastError: string | null };

/**
 * Open Razorpay Checkout for a subscription or order. A failed attempt keeps the popup open so the customer can
 * retry with another method, so failures are only remembered; the promise settles when they pay or close it.
 */
export async function payWithRazorpay(session: CheckoutSession): Promise<CheckoutResult> {
  await loadScript();
  return new Promise((resolve) => {
    let lastError: string | null = null;
    const rzp = new window.Razorpay!({
      key: session.key_id,
      // auto-renewing subscription, or a one-time order for a prepaid pass
      ...(session.mode === "subscription"
        ? { subscription_id: session.subscription_id }
        : { order_id: session.order_id, amount: session.amount, currency: session.currency }),
      name: session.name,
      description: session.description,
      prefill: { name: session.prefill.name, email: session.prefill.email ?? undefined },
      theme: { color: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2a78d6" },
      handler: (r: RazorpaySuccess) => resolve({ paid: r }),
      modal: { ondismiss: () => resolve({ closed: true, lastError }), confirm_close: true },
    });
    rzp.on("payment.failed", (r) => { lastError = r.error?.description || "Payment failed. No money was taken."; });
    rzp.open();
  });
}
