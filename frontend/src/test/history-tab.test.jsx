import { screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import HistoryTab from '../components/history/HistoryTab.jsx'
import { createSession } from './fixtures.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import { getSessions } from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  getSessions: vi.fn(),
}))

describe('HistoryTab', () => {
  it('shows a loading state while sessions are loading', () => {
    getSessions.mockReturnValue(new Promise(() => {}))

    renderWithProviders(<HistoryTab />)

    expect(screen.getByText('Loading sessions...')).toBeInTheDocument()
  })

  it('shows an error state when the history query fails', async () => {
    getSessions.mockRejectedValue(new Error('failed'))

    renderWithProviders(<HistoryTab />)

    expect(await screen.findByText('Unable to load history.')).toBeInTheDocument()
  })

  it('shows the empty state when no sessions exist', async () => {
    getSessions.mockResolvedValue([])

    renderWithProviders(<HistoryTab />)

    expect(await screen.findByText('No sessions have been recorded yet.')).toBeInTheDocument()
  })

  it('summarizes the loaded sessions', async () => {
    getSessions.mockResolvedValue([
      createSession({
        id: 'session-1',
        attributes_created: 1,
      }),
      createSession({
        id: 'session-2',
        session_type: 'interview',
        attributes_created: 2,
      }),
    ])

    renderWithProviders(<HistoryTab />)

    await waitFor(() => {
      expect(screen.getByText('2 sessions · 3 attributes created')).toBeInTheDocument()
    })

    expect(screen.getByText('Freeform session')).toBeInTheDocument()
    expect(screen.getByText('Guided interview')).toBeInTheDocument()
  })
})
