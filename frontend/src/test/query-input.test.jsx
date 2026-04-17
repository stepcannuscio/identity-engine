import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import QueryInput from '../components/query/QueryInput.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'

describe('QueryInput', () => {
  it('sends the current value when Enter is pressed', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()

    renderWithProviders(
      <QueryInput
        value="Tell me about my goals"
        onChange={vi.fn()}
        onSend={onSend}
        onAbort={vi.fn()}
        isStreaming={false}
      />,
    )

    await user.click(screen.getByPlaceholderText('Ask anything about yourself...'))
    await user.keyboard('{Enter}')

    expect(onSend).toHaveBeenCalledWith('Tell me about my goals')
  })

  it('allows Shift+Enter without sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()

    renderWithProviders(
      <QueryInput
        value="Line one"
        onChange={vi.fn()}
        onSend={onSend}
        onAbort={vi.fn()}
        isStreaming={false}
      />,
    )

    await user.click(screen.getByPlaceholderText('Ask anything about yourself...'))
    await user.keyboard('{Shift>}{Enter}{/Shift}')

    expect(onSend).not.toHaveBeenCalled()
  })

  it('clears the input through the clear button and disables whitespace-only sends', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()

    const { rerender } = renderWithProviders(
      <QueryInput
        value="   "
        onChange={onChange}
        onSend={vi.fn()}
        onAbort={vi.fn()}
        isStreaming={false}
      />,
    )

    expect(screen.getByRole('button', { name: 'Send query' })).toBeDisabled()

    rerender(
      <QueryInput
        value="A draft"
        onChange={onChange}
        onSend={vi.fn()}
        onAbort={vi.fn()}
        isStreaming={false}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Clear input' }))

    expect(onChange).toHaveBeenCalledWith('')
  })

  it('shows a stop button while streaming', async () => {
    const user = userEvent.setup()
    const onAbort = vi.fn()

    renderWithProviders(
      <QueryInput
        value="Streaming"
        onChange={vi.fn()}
        onSend={vi.fn()}
        onAbort={onAbort}
        isStreaming
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Stop response' }))

    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(screen.getByPlaceholderText('Ask anything about yourself...')).toHaveAttribute(
      'readonly',
    )
  })
})
