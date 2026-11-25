# Polar setup (created 2026-11-17 with the Polar CLI)

Organization: `hack` (rename to ClipHeist in Polar settings), production environment.

| Plan | Product ID | Normal | Launch discount (code) | Discount ID | Checkout link |
|---|---|---|---|---|---|
| Starter (50 clips) | ff997522-b4f2-4c33-a7d0-d98b238546fa | $25 | $10 off (LAUNCHSTARTER) | 7c7c6fb5-ce8f-40ef-9197-886d4f814a67 | https://buy.polar.sh/polar_cl_skDmEUMEYsVHbUBnFFDmeIvml0sTtF6HHQXqY4KKx0V |
| Pro (150 clips) | a60e64fa-bc7f-4f4a-8be9-eee07a273737 | $59 | $24 off (LAUNCHPRO) | 871b6799-0b79-410c-9ce3-e002dcfaaf4a | https://buy.polar.sh/polar_cl_Qa5zkDY2XU18Dq8svVMbEaryRM1eCBQDguumf2EsgEd |
| Premium (500 clips) | c83dfb79-4eaf-48e0-832e-c99a351f8d1e | $119 | $50 off (LAUNCHPREMIUM) | 7762242a-d709-45bc-8974-f83e06cb8393 | https://buy.polar.sh/polar_cl_h7ZpBiNtiWdV7o2rtsNi7TF0ad6PWmnHjlh3T2iVleY |

- Product metadata: `plan` (starter/pro/premium) and `clips` (50/150/500). The backend reads `clips` from the
  `order.paid` webhook to add clip credits.
- Discounts end `2026-11-23T07:59:00Z` (Sun 22 Nov, 11:59 PM PT). After that the links charge the normal price
  automatically.
- Each link has the discount pre-applied, success URL `https://clipheist.site/pricing?checkout=success&plan=<plan>&checkout_id={CHECKOUT_ID}`,
  return URL `https://clipheist.site/pricing`.
- Status on creation: Polar shows "hack is in test mode". Real payments start once the account is approved
  (payout account + review in the Polar dashboard).

Still to do: webhook endpoint on the backend (`order.paid` → credits), Polar account approval, TrustMRR connection.
