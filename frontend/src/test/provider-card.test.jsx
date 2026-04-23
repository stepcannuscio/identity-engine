import { render, screen } from '@testing-library/react'
import ProviderCard from '../components/settings/ProviderCard.jsx'

const urlProvider = {
  provider: 'private_server',
  label: 'Private Server',
  deployment: 'local',
  trust_boundary: 'self_hosted',
  available: false,
  auth_strategy: 'url',
  credential_fields: [
    {
      name: 'server_url',
      label: 'Server URL',
      secret: false,
      input_type: 'text',
      placeholder: 'http://localhost:11434',
    },
  ],
  description: 'A self-hosted server you control.',
  setup_hint: 'Run Ollama locally and enter the URL above.',
  reason: null,
  model: null,
}

function renderCard(overrides = {}) {
  return render(
    <ProviderCard
      provider={urlProvider}
      values={{ server_url: '' }}
      isSaving={false}
      isTesting={false}
      isSelected={false}
      testResult={null}
      onFieldChange={vi.fn()}
      onSave={vi.fn()}
      onTest={vi.fn()}
      {...overrides}
    />,
  )
}

describe('ProviderCard (url auth)', () => {
  it('renders url input and save/test buttons', () => {
    renderCard()
    expect(screen.getByPlaceholderText('http://localhost:11434')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save server URL' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeInTheDocument()
  })

  it('shows selected badge when isSelected is true', () => {
    renderCard({ isSelected: true })
    expect(screen.getByText('selected')).toBeInTheDocument()
  })

  it('shows connected result with latency when reachable', () => {
    renderCard({
      values: { server_url: 'http://localhost:11434' },
      testResult: { reachable: true, latency_ms: 55, model_available: true },
    })
    expect(screen.getByText(/Connected.*55ms.*model ready/)).toBeInTheDocument()
  })

  it('shows unreachable error when test fails', () => {
    renderCard({
      values: { server_url: 'http://localhost:11434' },
      testResult: { reachable: false, error: 'connection refused' },
    })
    expect(screen.getByText(/Unreachable: connection refused/)).toBeInTheDocument()
  })
})
