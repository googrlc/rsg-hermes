# RSG OpenClaw

**The Data Entry & User Experience Layer for the RSG Insurance Platform**

OpenClaw is the dedicated frontend application for all ad-hoc data entry, contact management, and user interactions. It connects to Supabase for data persistence and triggers Hermes workflows for complex business logic.

## 🏗 Architecture Position

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│  OpenClaw   │ ───► │   Supabase   │ ◄──► │   Hermes    │
│  (Frontend) │      │  (Database)  │      │ (Backend/BI)│
│  Data Entry │      │   Storage    │      │Reconciliation│
└─────────────┘      └──────────────┘      └─────────────┘
       │                      │                     │
       │                      ▼                     ▼
       │              ┌──────────────┐      ┌─────────────┐
       └────────────► │  EspoCRM     │      │  NowCerts   │
                      │   (CRM)      │      │(Policy Data)│
                      └──────────────┘      └─────────────┘
```

## 🎯 Responsibilities

### ✅ OpenClaw Handles:
- **Contact/Account/Lead Forms**: Create, edit, validate
- **Bulk Operations**: CSV imports, mass updates
- **Real-time Validation**: Field-level validation before submission
- **User Dashboard**: Personal tasks, recent activities
- **Search & Lookup**: Fast contact/policy search
- **File Uploads**: Document management UI

### ❌ OpenClaw Does NOT Handle:
- Cross-system reconciliation (Hermes)
- Complex business logic (Hermes)
- Cron jobs & scheduled syncs (Hermes)
- Direct EspoCRM/NowCerts API calls (Hermes)
- Analytics & BI dashboards (Hermes)

## 🚀 Quick Start

### Prerequisites
- Node.js 18+
- npm or yarn
- Supabase project credentials
- Hermes API endpoint

### Installation

```bash
# Clone the repository
git clone https://github.com/googrlc/rsg-openclaw.git
cd rsg-openclaw

# Install dependencies
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with your credentials

# Start development server
npm run dev
```

### Environment Variables

```env
# Supabase
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key

# Hermes API
HERMES_API_URL=https://hermes.yourdomain.com/api
HERMES_API_KEY=your_api_key

# App Configuration
NEXT_PUBLIC_APP_NAME="RSG OpenClaw"
NEXT_PUBLIC_APP_VERSION="1.0.0"
```

## 📁 Project Structure

```
rsg-openclaw/
├── app/
│   ├── components/          # Reusable UI components
│   │   ├── forms/           # Contact, Account, Lead forms
│   │   ├── tables/          # Data tables with sorting/filtering
│   │   ├── search/          # Search components
│   │   └── ui/              # Base UI components (buttons, inputs)
│   ├── pages/               # Application pages
│   │   ├── contacts/        # Contact management
│   │   ├── accounts/        # Account management
│   │   ├── leads/           # Lead management
│   │   ├── policies/        # Policy lookup & view
│   │   ├── dashboard/       # User dashboard
│   │   └── bulk/            # Bulk operations
│   ├── hooks/               # Custom React hooks
│   │   ├── useSupabase.ts   # Supabase integration
│   │   ├── useHermes.ts     # Hermes API integration
│   │   └── useValidation.ts # Form validation
│   ├── services/            # API service layers
│   │   ├── supabase.ts      # Supabase client
│   │   ├── hermes.ts        # Hermes API client
│   │   └── validators.ts    # Validation logic
│   └── utils/               # Utility functions
├── public/                  # Static assets
├── docs/                    # Documentation
├── .env.example             # Environment template
├── package.json             # Dependencies
└── README.md                # This file
```

## 🔌 Integration with Hermes

OpenClaw communicates with Hermes for:
1. **Workflow Triggers**: Start reconciliation, sync policies
2. **Data Enrichment**: Fetch enhanced data from EspoCRM/NowCerts
3. **Analytics**: Dashboard metrics, reports
4. **Notifications**: Slack alerts, email notifications

### Example: Creating a Contact

```typescript
// app/services/supabase.ts
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)

export async function createContact(data: ContactData) {
  // 1. Validate locally
  const validation = validateContact(data)
  if (!validation.valid) throw new Error(validation.errors.join(', '))
  
  // 2. Insert into Supabase
  const { data: contact, error } = await supabase
    .from('contacts')
    .insert(data)
    .select()
    .single()
  
  if (error) throw error
  
  // 3. Trigger Hermes workflow for enrichment
  await fetch(`${process.env.HERMES_API_URL}/workflows/enrich-contact`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${process.env.HERMES_API_KEY}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ contact_id: contact.id })
  })
  
  return contact
}
```

## 📊 Dashboard Examples

OpenClaw provides operational dashboards, while Hermes provides BI dashboards:

### OpenClaw Dashboards (Operational)
- Today's data entry tasks
- Pending validations
- Recent contacts/accounts created
- Bulk import status

### Hermes Dashboards (Business Intelligence)
- Policy sync status across systems
- Reconciliation discrepancies
- Client update summaries
- SLA compliance metrics

## 🧪 Testing

```bash
# Run unit tests
npm test

# Run e2e tests
npm run test:e2e

# Run linting
npm run lint
```

## 🚢 Deployment

### Build for Production

```bash
npm run build
```

### Deploy to Server

```bash
# SSH to server
ssh user@server

# Navigate to app directory
cd /var/www/openclaw

# Pull latest changes
git pull origin main

# Install dependencies
npm ci --production

# Build
npm run build

# Restart service (PM2 example)
pm2 restart openclaw
```

### Docker Deployment

```dockerfile
FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --production

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
```

## 🔐 Security

- All API calls use JWT authentication via Supabase
- Role-based access control (RBAC) enforced
- Input validation on all forms
- Rate limiting on API calls
- Audit logging for all data changes

## 📝 Development Guidelines

1. **Forms First**: Always create a form component for data entry
2. **Validate Early**: Client-side validation before API calls
3. **Error Handling**: Graceful error messages with retry options
4. **Loading States**: Show loading indicators for async operations
5. **Accessibility**: WCAG 2.1 AA compliance required

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

Proprietary - RSG Insurance

## 🆘 Support

For issues or questions:
- Check existing issues in GitHub
- Contact the Hermes team for API-related questions
- Review documentation in `/docs`

---

**Remember**: OpenClaw is for data entry and UX. For complex reconciliation, analytics, or cross-system workflows, use Hermes.
