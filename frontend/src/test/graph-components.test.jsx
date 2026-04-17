import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AttributeCard from '../components/graph/AttributeCard.jsx'
import DomainSection from '../components/graph/DomainSection.jsx'
import { createAttribute } from './fixtures.js'

describe('AttributeCard', () => {
  it('shows routing, status, correction actions, and edit affordances', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-17T12:00:00Z'))
    const onConfirm = vi.fn()
    const onEdit = vi.fn()
    const onReject = vi.fn()

    render(
      <AttributeCard
        attribute={createAttribute({
          routing: 'external_ok',
          status: 'confirmed',
          last_confirmed: '2026-04-16T12:00:00Z',
        })}
        onConfirm={onConfirm}
        onEdit={onEdit}
        onReject={onReject}
      />,
    )

    expect(screen.getByText('external ok')).toBeInTheDocument()
    expect(screen.getByText('confirmed')).toBeInTheDocument()
    expect(screen.getByText('last confirmed 1 day ago')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))
    fireEvent.click(screen.getByRole('button', { name: 'reject' }))
    fireEvent.click(screen.getByRole('button', { name: 'edit' }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onReject).toHaveBeenCalledTimes(1)
    expect(onEdit).toHaveBeenCalledTimes(1)
  })
})

describe('DomainSection', () => {
  it('renders collapsed and expanded states and hands attributes back for editing', async () => {
    const user = userEvent.setup()
    const attribute = createAttribute({
      id: 'attribute-1',
      value: 'Honest relationships matter most',
    })
    const onEdit = vi.fn()
    const onToggle = vi.fn()

    const { rerender } = render(
      <DomainSection
        domain="values"
        attributes={[attribute]}
        expanded={false}
        onConfirm={vi.fn()}
        onReject={vi.fn()}
        onToggle={onToggle}
        onEdit={onEdit}
      />,
    )

    expect(screen.queryByText('Honest relationships matter most')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button'))
    expect(onToggle).toHaveBeenCalledTimes(1)

    rerender(
      <DomainSection
        domain="values"
        attributes={[attribute]}
        expanded
        onConfirm={vi.fn()}
        onReject={vi.fn()}
        onToggle={onToggle}
        onEdit={onEdit}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'edit' }))
    expect(screen.getByText('Honest relationships matter most')).toBeInTheDocument()
    expect(onEdit).toHaveBeenCalledWith(attribute)
  })
})
