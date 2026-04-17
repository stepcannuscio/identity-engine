import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import GraphTab from '../components/graph/GraphTab.jsx'
import { createAttribute, createDomain } from './fixtures.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import { useAttributes } from '../hooks/useAttributes.js'

vi.mock('../hooks/useAttributes.js', () => ({
  useAttributes: vi.fn(),
}))

function mockAttributes(overrides = {}) {
  useAttributes.mockReturnValue({
    domains: [
      createDomain({ domain: 'personality', attribute_count: 1 }),
      createDomain({ domain: 'values', attribute_count: 1 }),
    ],
    attributes: [
      createAttribute({
        id: 'attribute-1',
        domain: 'personality',
        label: 'reflection_style',
        value: 'Reflective and deliberate',
      }),
      createAttribute({
        id: 'attribute-2',
        domain: 'values',
        label: 'honesty',
        value: 'Honest relationships matter most',
      }),
    ],
    isLoading: false,
    isError: false,
    refreshAttributes: vi.fn(),
    ...overrides,
  })
}

describe('GraphTab', () => {
  it('renders loading, error, and empty states', () => {
    mockAttributes({ isLoading: true, attributes: [], domains: [] })
    const { rerender } = renderWithProviders(<GraphTab />)

    expect(screen.getByText('Loading attributes...')).toBeInTheDocument()

    mockAttributes({ isLoading: false, isError: true, attributes: [], domains: [] })
    rerender(<GraphTab />)
    expect(screen.getByText('Unable to load the identity graph.')).toBeInTheDocument()

    mockAttributes({ isLoading: false, isError: false, attributes: [], domains: [] })
    rerender(<GraphTab />)
    expect(screen.getByText('No attributes match this view yet.')).toBeInTheDocument()
  })

  it('filters attributes via search and sidebar domain selection', async () => {
    const user = userEvent.setup()
    mockAttributes()

    renderWithProviders(<GraphTab />)

    expect(screen.queryByText('Honest relationships matter most')).not.toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Search attributes'), 'honest')

    expect(screen.getByText('Honest relationships matter most')).toBeInTheDocument()
    expect(screen.queryByText('Reflective and deliberate')).not.toBeInTheDocument()

    await user.clear(screen.getByPlaceholderText('Search attributes'))
    await user.click(screen.getByRole('button', { name: /^values1$/i }))

    expect(screen.getByText('Honest relationships matter most')).toBeInTheDocument()
    expect(screen.queryByText('Reflective and deliberate')).not.toBeInTheDocument()
  })

  it('filters attributes via the mobile domain select', async () => {
    const user = userEvent.setup()
    mockAttributes()

    renderWithProviders(<GraphTab />)

    await user.selectOptions(screen.getByRole('combobox'), 'personality')

    expect(screen.getByText('Reflective and deliberate')).toBeInTheDocument()
    expect(screen.queryByText('Honest relationships matter most')).not.toBeInTheDocument()
  })

  it('defaults new attributes to the selected domain', async () => {
    const user = userEvent.setup()
    mockAttributes()

    renderWithProviders(<GraphTab />)

    await user.click(screen.getByRole('button', { name: /^values1$/i }))
    await user.click(screen.getByRole('button', { name: 'Add attribute' }))

    expect(screen.getByLabelText('Domain')).toHaveValue('values')
  })
})
