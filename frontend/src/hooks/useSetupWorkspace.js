import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  saveProviderCredentials,
  saveSetupProfile,
  updateSecurityCheckOverride,
} from '../api/endpoints.js'
import { useAppState } from '../store/appState.js'

function computeRecommendedProfileCode(providers, privacyPreference) {
  const localReady = providers.some(
    (provider) =>
      provider.deployment === 'local' &&
      provider.trust_boundary === 'self_hosted' &&
      provider.available,
  )
  const externalReady = providers.some(
    (provider) => provider.deployment === 'external' && provider.available,
  )

  if (privacyPreference === 'privacy_first') {
    if (localReady) {
      return 'private_local_first'
    }
    return externalReady ? 'external_assist' : 'private_local_first'
  }

  if (privacyPreference === 'capability_first') {
    if (externalReady) {
      return 'external_assist'
    }
    return localReady ? 'private_local_first' : 'external_assist'
  }

  if (localReady && externalReady) {
    return 'balanced_hybrid'
  }
  if (localReady) {
    return 'private_local_first'
  }
  if (externalReady) {
    return 'external_assist'
  }
  return 'private_local_first'
}

function buildCredentialValues(providers) {
  return Object.fromEntries(
    providers.map((provider) => [
      provider.provider,
      Object.fromEntries((provider.credential_fields ?? []).map((field) => [field.name, ''])),
    ]),
  )
}

function buildProviderSelections(bootstrap) {
  return Object.fromEntries(
    (bootstrap?.profiles ?? []).map((profile) => {
      const preferredProvider = bootstrap?.preferred_provider
      const fallback =
        preferredProvider && profile.provider_options.includes(preferredProvider)
          ? preferredProvider
          : profile.recommended_provider ?? profile.provider_options?.[0] ?? null
      return [profile.code, fallback]
    }),
  )
}

export function useSetupWorkspace({ bootstrapQuery }) {
  const queryClient = useQueryClient()
  const {
    addToast,
    backend,
    activeProfile,
    preferredProvider,
    securityPosture,
  } = useAppState()
  const [isSaving, setIsSaving] = useState(false)
  const [pendingSecurityCode, setPendingSecurityCode] = useState(null)
  const [privacyPreferenceDraft, setPrivacyPreferenceDraft] = useState('balanced')
  const [providerSelections, setProviderSelections] = useState({})
  const [credentialValues, setCredentialValues] = useState({})

  const bootstrap = bootstrapQuery.data
  const providers = bootstrap?.providers ?? []
  const posture = bootstrap?.security_posture ?? securityPosture
  const savedProfile = bootstrap?.active_profile ?? activeProfile
  const savedProvider = bootstrap?.preferred_provider ?? preferredProvider
  const recommendedProfileCode = computeRecommendedProfileCode(
    providers,
    privacyPreferenceDraft,
  )
  const selectedProfileCode = savedProfile ?? recommendedProfileCode

  useEffect(() => {
    if (!bootstrap) {
      return
    }
    setPrivacyPreferenceDraft(bootstrap.privacy_preference ?? 'balanced')
    setProviderSelections(buildProviderSelections(bootstrap))
    setCredentialValues((current) => {
      if (Object.keys(current).length > 0) {
        return current
      }
      return buildCredentialValues(bootstrap.providers ?? [])
    })
  }, [bootstrap])

  const refreshBootstrap = async () => {
    await queryClient.invalidateQueries({ queryKey: ['teachBootstrap'] })
    await queryClient.invalidateQueries({ queryKey: ['attributes'] })
    await queryClient.invalidateQueries({ queryKey: ['domains'] })
  }

  const handleProfileSave = async (profileCode, markComplete = false) => {
    const profile = bootstrap?.profiles?.find((item) => item.code === profileCode)
    const preferred =
      providerSelections[profileCode] ??
      profile?.recommended_provider ??
      profile?.provider_options?.[0] ??
      null

    setIsSaving(true)
    try {
      await saveSetupProfile({
        profile: profileCode,
        privacy_preference: privacyPreferenceDraft,
        preferred_provider: preferred,
        preferred_backend: profile?.default_backend ?? backend,
        onboarding_completed: markComplete,
      })
      await refreshBootstrap()
      addToast({
        message: markComplete ? 'Onboarding preferences saved.' : 'Configuration updated.',
        tone: 'success',
      })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? 'Unable to save your configuration right now.',
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleProviderSave = async (provider) => {
    setIsSaving(true)
    try {
      await saveProviderCredentials(provider, credentialValues[provider] ?? {})
      setCredentialValues((current) => ({
        ...current,
        [provider]: Object.fromEntries(
          Object.keys(current[provider] ?? {}).map((fieldName) => [fieldName, '']),
        ),
      }))
      await refreshBootstrap()
      addToast({ message: `${provider} credentials saved.`, tone: 'success' })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? `Unable to save ${provider} credentials.`,
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleSecurityCheckComplete = async (checkCode, completed) => {
    setPendingSecurityCode(checkCode)
    try {
      await updateSecurityCheckOverride(checkCode, { completed })
      await refreshBootstrap()
      addToast({
        message: completed
          ? 'Security check marked complete.'
          : 'Security check returned to update needed.',
        tone: 'success',
      })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? 'Unable to update that security check.',
      })
    } finally {
      setPendingSecurityCode(null)
    }
  }

  return {
    bootstrap,
    credentialValues,
    handleProfileSave,
    handleProviderSave,
    handleSecurityCheckComplete,
    isSaving,
    pendingSecurityCode,
    posture,
    privacyPreferenceDraft,
    providers,
    providerSelections,
    recommendedProfileCode,
    savedProfile,
    savedProvider,
    selectedProfileCode,
    setCredentialValues,
    setPrivacyPreferenceDraft,
    setProviderSelections,
  }
}
