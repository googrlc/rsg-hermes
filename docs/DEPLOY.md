# Hermes CRM Form - Production Deployment Guide

## Environment Variable Configuration

The form now supports automatic configuration via environment variables for production deployment.

### Supported Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `HERMES_SUPABASE_URL` | Supabase Function URL | `https://xyz.supabase.co/functions/v1/hermes-crm-proxy` |
| `HERMES_SUPABASE_KEY` | Supabase Anon/Publishable Key | `eyJhbGciOiJIUzI1NiIs...` |
| `HERMES_WEBHOOK_URL` | Direct the CRM Webhook URL | `https://crm.example.com/api/v1/...` |
| `HERMES_TRANSPORT_MODE` | Transport mode: `proxy` or `direct` | `proxy` |

### Deployment Methods

#### Method 1: Static Site with Build-Time Injection

For Vite, Webpack, or similar build tools:

```javascript
// vite.config.js
export default {
  define: {
    'process.env.HERMES_SUPABASE_URL': JSON.stringify(process.env.HERMES_SUPABASE_URL),
    'process.env.HERMES_SUPABASE_KEY': JSON.stringify(process.env.HERMES_SUPABASE_KEY),
    'process.env.HERMES_WEBHOOK_URL': JSON.stringify(process.env.HERMES_WEBHOOK_URL),
    'process.env.HERMES_TRANSPORT_MODE': JSON.stringify(process.env.HERMES_TRANSPORT_MODE),
  }
}
```

#### Method 2: Server-Side HTML Injection

For Node.js/Express servers:

```javascript
app.get('/', (req, res) => {
  const html = fs.readFileSync('./index.html', 'utf8')
    .replace(
      '</head>',
      `<script>window.HERMES_CONFIG = {
        supabaseUrl: "${process.env.HERMES_SUPABASE_URL}",
        supabaseKey: "${process.env.HERMES_SUPABASE_KEY}",
        webhookUrl: "${process.env.HERMES_WEBHOOK_URL}",
        transportMode: "${process.env.HERMES_TRANSPORT_MODE || 'proxy'}"
      };</script></head>`
    );
  res.send(html);
});
```

#### Method 3: Docker Environment

```dockerfile
ENV HERMES_SUPABASE_URL=https://xyz.supabase.co/functions/v1/hermes-crm-proxy
ENV HERMES_SUPABASE_KEY=your-anon-key
ENV HERMES_WEBHOOK_URL=https://crm.example.com/api/v1/webhook
ENV HERMES_TRANSPORT_MODE=proxy
```

#### Method 4: GitHub Pages / Static Hosting

Create a small JavaScript file that loads before the main script:

```html
<!-- config.js -->
<script>
  window.HERMES_CONFIG = {
    supabaseUrl: "YOUR_SUPABASE_URL",
    supabaseKey: "YOUR_SUPABASE_KEY",
    webhookUrl: "YOUR_WEBHOOK_URL",
    transportMode: "proxy"
  };
</script>
```

Include it in your HTML before the main script:
```html
<script src="config.js"></script>
```

## Security Notes

- **Never commit actual keys to version control**
- Use `.env` files locally (add to `.gitignore`)
- For production, use your hosting platform's secret management
- The form uses localStorage for user convenience but sensitive keys should come from env vars

## Verification

After deployment, the form will display:
- ✓ Green checkmark if environment variables are loaded successfully
- ⚠ Yellow warning if manual configuration is required

The input fields will be pre-populated with values from environment variables.
