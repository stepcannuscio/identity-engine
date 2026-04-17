import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import QueryTab from '../components/query/QueryTab.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'
import { useStream } from '../hooks/useStream.js'

vi.mock('../hooks/useStream.js', () => ({
  useStream: vi.fn(),
}))

describe('QueryTab with mocked stream hook', () => {
  it('prevents duplicate sends while streaming and preserves partial output on abort', async () => {
    const user = userEvent.setup()
    let finishStream
    const pendingStream = new Promise((resolve) => {
      finishStream = resolve
    })
    const abortStream = vi.fn(() => {
      finishStream()
    })
    const streamQuery = vi.fn(async ({ onToken, onAbort }) => {
      onToken?.('Partial reply')
      await pendingStream
      onAbort?.()
    })

    useStream.mockReturnValue({
      streamQuery,
      abortStream,
    })

    renderWithProviders(<QueryTab />, {
      appState: {
        token: 'session-token',
      },
    })

    const input = screen.getByPlaceholderText('Ask anything about yourself...')
    await user.type(input, 'Start streaming')
    await user.click(screen.getByRole('button', { name: 'Send query' }))

    expect(await screen.findByRole('button', { name: 'Stop response' })).toBeInTheDocument()

    input.focus()
    await user.keyboard('{Enter}')
    expect(streamQuery).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Stop response' }))
    expect(abortStream).toHaveBeenCalledTimes(1)

    await waitFor(() => {
      expect(screen.getByText('Partial reply')).toBeInTheDocument()
    })

    expect(document.querySelector('.message.streaming')).toBeNull()
  })
})
