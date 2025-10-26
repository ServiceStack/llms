import json
import aiohttp

async def download_urls(urls):
    async def fetch_and_save(session, url, filename):
        print(f"Downloading {url} to {filename}")
        headers = {
          'Accept': 'application/json',
        }
        async with session.get(url, headers=headers) as response:
            content = await response.text()
            with open(filename, 'w') as f:
                format_json = json.loads(content)
                f.write(json.dumps(format_json, indent=2))

    async with aiohttp.ClientSession() as session:
        for filename, url in urls.items():
            await fetch_and_save(session, url, filename)

def price_to_string(price: float | int | str | None) -> str | None:
    """Convert numeric price to string without scientific notation.

    Detects and rounds up numbers with recurring 9s (e.g., 0.00014999999999999999)
    to avoid floating-point precision artifacts.
    """
    if price is None or price == 0 or price == "0":
        return "0"
    try:
        price_float = float(price)
        # Format with enough decimal places to avoid scientific notation
        formatted = format(price_float, '.20f')

        # Detect recurring 9s pattern (e.g., "...9999999")
        # If we have 4 or more consecutive 9s, round up
        if '9999' in formatted:
            # Round up by adding a small amount and reformatting
            # Find the position of the 9s to determine precision
            import decimal
            decimal.getcontext().prec = 28
            d = decimal.Decimal(str(price_float))
            # Round to one less decimal place than where the 9s start
            nines_pos = formatted.find('9999')
            if nines_pos > 0:
                # Round up at the position before the 9s
                decimal_places = nines_pos - formatted.find('.') - 1
                if decimal_places > 0:
                    quantize_str = '0.' + '0' * (decimal_places - 1) + '1'
                    d = d.quantize(decimal.Decimal(quantize_str), rounding=decimal.ROUND_UP)
                    result = str(d)
                    # Remove trailing zeros
                    if '.' in result:
                        result = result.rstrip('0').rstrip('.')
                    return result

        # Normal case: strip trailing zeros
        return formatted.rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return None
