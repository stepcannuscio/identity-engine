import SetupWorkspace from './SetupWorkspace.jsx'

export default function SettingsTab({ bootstrapQuery }) {
  if (bootstrapQuery.isLoading) {
    return (
      <section className="teach-tab">
        <div className="screen-state">
          <p>Loading Settings...</p>
        </div>
      </section>
    )
  }

  return (
    <section className="teach-tab">
      <div className="teach-hero">
        <p className="eyebrow">Settings</p>
        <h1>Privacy and system setup</h1>
        <p>
          Update your model configuration, provider credentials, and local security
          confirmations without crowding the Teach workflow.
        </p>
      </div>

      <SetupWorkspace bootstrapQuery={bootstrapQuery} />
    </section>
  )
}
