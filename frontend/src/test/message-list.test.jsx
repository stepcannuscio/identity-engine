import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MessageList from '../components/query/MessageList.jsx'

describe('MessageList', () => {
  it('renders the empty prompt when there are no messages', () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={null}
        toasts={[]}
        onDismissToast={vi.fn()}
      />,
    )

    expect(
      screen.getByText('What would you like to know about yourself?'),
    ).toBeInTheDocument()
  })

  it('renders and dismisses warning toasts', async () => {
    const user = userEvent.setup()
    const onDismissToast = vi.fn()

    render(
      <MessageList
        messages={[]}
        streamingMessage={null}
        toasts={[{ id: 'toast-1', message: 'Sensitive content detected' }]}
        onDismissToast={onDismissToast}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Sensitive content detected' }))

    expect(onDismissToast).toHaveBeenCalledWith('toast-1')
  })

  it('scrolls to the latest content when messages change', () => {
    const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView')

    const { rerender } = render(
      <MessageList
        messages={[]}
        streamingMessage={null}
        toasts={[]}
        onDismissToast={vi.fn()}
      />,
    )

    rerender(
      <MessageList
        messages={[{ role: 'user', content: 'Hello' }]}
        streamingMessage={null}
        toasts={[]}
        onDismissToast={vi.fn()}
      />,
    )

    expect(scrollIntoView).toHaveBeenCalled()
  })
})
