import { useEffect, useState } from 'react'
import { api, errorMessage } from '@/lib/api'
import type { Mention, ProjectProfile, ProjectSource } from '@/lib/types'
import { useAuthStore } from '@/store/auth'
import { useDraftStore } from '@/store/draft'
import { useSpacesStore } from '@/store/spaces'
import { ENV_LABELS, ENV_ORDER, useCodeProjectStore } from '@/store/codeProjects'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function ProjectContextPanel({ mention }: { mention: Mention }) {
  const enabled = useAuthStore((s) => s.me?.features?.project_context === true)
  const optedIn = useAuthStore((s) => s.me?.preferences.project_context_opt_in === true)
  const choice = useDraftStore((s) => s.active?.projectChoice)
  const selectProject = useDraftStore((s) => s.selectProject)
  const toggleSource = useDraftStore((s) => s.toggleProjectSource)
  const setConsent = useDraftStore((s) => s.setProjectConsent)
  const spaces = useSpacesStore((s) => s.items)
  const projects = useCodeProjectStore((s) => s.projects)
  const ensureCodeLoaded = useCodeProjectStore((s) => s.ensureLoaded)
  const [profiles, setProfiles] = useState<ProjectProfile[]>([])
  const [editing, setEditing] = useState<ProjectProfile | null>(null)
  const [name, setName] = useState('')
  const [sources, setSources] = useState<ProjectSource[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!enabled || !optedIn) return
    let active = true
    void ensureCodeLoaded()
    api.projectProfiles().then((result) => { if (active) setProfiles(result.profiles) })
      .catch((err) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [enabled, optedIn, ensureCodeLoaded])

  if (!enabled) return null

  const save = async () => {
    const epoch = useDraftStore.getState().active?.epoch
    setBusy(true)
    setError('')
    try {
      const saved = await api.saveProjectProfile({ name, sources, ...(editing ? { revision: editing.revision } : {}) }, editing?.id)
      setProfiles((old) => [...old.filter((profile) => profile.id !== saved.id), saved])
      setEditing(saved)
      if (useDraftStore.getState().active?.epoch === epoch) selectProject(saved)
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return (
    <section className="space-y-2 border-b border-border p-3 text-xs" aria-label="專案脈絡">
      <h3 className="font-semibold">專案脈絡</h3>
      <p className="text-muted-foreground">來源只保存關聯。每題明選並確認目標才讀取，草稿未語意查證。</p>
      <label className="flex gap-2">
        <input type="checkbox" checked={optedIn} onChange={async (event) => {
          setBusy(true)
          try {
            await api.projectOptIn(event.target.checked)
            selectProject(null)
            await useAuthStore.getState().refreshMe()
          } catch (err) { setError(errorMessage(err)) }
          finally { setBusy(false) }
        }} disabled={busy} />
        啟用我的專案脈絡
      </label>
      {optedIn ? <>
        <label className="block">本題專案
          <select aria-label="本題專案" className="mt-1 w-full rounded border bg-background p-1" value={choice?.profile.id ?? ''}
            onChange={(event) => {
              const profile = profiles.find((p) => String(p.id) === event.target.value) ?? null
              selectProject(profile)
            }}>
            <option value="">未選擇，使用一般草稿</option>
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · r{profile.revision}</option>)}
          </select>
        </label>
        {choice ? <div className="space-y-2">
          <p>本題來源預設僅 metadata，不讀內容。勾選才納入回話脈絡。</p>
          {choice.profile.sources.map((source) => <label key={source.id} className="flex items-start gap-2">
            <input type="checkbox" checked={choice.approvedIds.includes(source.id)} onChange={() => toggleSource(source.id)} />
            <span>{source.label} · {source.kind === 'chat' ? source.category === 'pm' ? 'PM' : 'Space' : ENV_LABELS[source.environment]}
              <span className="block break-all text-muted-foreground">{source.kind === 'chat' ? source.space_id : source.paths.join('、') || '依關鍵字搜尋'}</span>
            </span>
          </label>)}
          <label className="flex items-start gap-2">
            <input aria-label="確認本題確切目標" type="checkbox" checked={Boolean(choice.consent)} onChange={(event) =>
              setConsent(event.target.checked ? { spaceId: mention.space_id, threadName: mention.thread_name ?? null } : null)} />
            <span>本題同意將勾選內容用於此目標的 Draft Reply
              <code className="block break-all">{mention.space_id} / {mention.thread_name ?? '無討論串'}</code>
            </span>
          </label>
        </div> : null}
        <details>
          <summary className="cursor-pointer py-1">管理專案來源</summary>
          <fieldset disabled={busy} className="space-y-2 pt-2">
            <select aria-label="編輯已存專案" className="w-full rounded border bg-background p-1" value={editing?.id ?? ''}
              onChange={(event) => {
                const profile = profiles.find((p) => String(p.id) === event.target.value) ?? null
                setEditing(profile); setName(profile?.name ?? ''); setSources(profile?.sources ?? [])
              }}>
              <option value="">建立新專案</option>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
            <Input aria-label="專案名稱" value={name} onChange={(e) => setName(e.target.value)} placeholder="專案名稱" />
            <select aria-label="新增 Space 來源" className="w-full rounded border bg-background p-1" value="" onChange={(event) => {
              const space = spaces.find((s) => s.id === event.target.value)
              if (space) setSources((old) => [...old, { id: crypto.randomUUID(), kind: 'chat', label: space.displayName, space_id: space.id, category: 'space' }])
            }}>
              <option value="">新增 Space／PM 來源</option>
              {spaces.map((space) => <option key={space.id} value={space.id}>{space.displayName}</option>)}
            </select>
            {projects.flatMap((project) => ENV_ORDER.filter((env) => project.branches[env]).map((env) =>
              <Button key={`${project.id}-${env}`} size="xs" variant="outline" onClick={() => setSources((old) =>
                [...old, { id: crypto.randomUUID(), kind: 'git', label: project.name, code_project_id: project.id, environment: env, paths: [] }])}>
                加入 {project.name} · {ENV_LABELS[env]}
              </Button>))}
            {sources.map((source) => <div key={source.id} className="space-y-1 rounded border p-2">
              <p>{source.label}{source.kind === 'git' ? ` · ${ENV_LABELS[source.environment]}` : ''}</p>
              {source.kind === 'chat' ? <label>來源類別
                <select aria-label={`${source.label} 類別`} value={source.category} onChange={(e) => {
                  const category = e.target.value === 'pm' ? 'pm' : 'space'
                  setSources((old) => old.map((s) => s.id === source.id && s.kind === 'chat' ? { ...s, category } : s))
                }}><option value="space">Space</option><option value="pm">PM</option></select>
              </label> : <Input aria-label={`${source.label} 相對路徑`} defaultValue={source.paths.join(', ')} placeholder="相對路徑，逗號分隔（選填）"
                onChange={(e) => setSources((old) => old.map((s) => s.id === source.id && s.kind === 'git'
                  ? { ...s, paths: e.target.value.split(',').map((p) => p.trim()).filter(Boolean) } : s))} />}
              <Button size="xs" variant="ghost" onClick={() => setSources((old) => old.filter((s) => s.id !== source.id))}>移除此來源</Button>
            </div>)}
            <Button size="sm" disabled={busy || !name.trim() || sources.length === 0 || sources.length > 12} onClick={() => void save()}>
              {editing ? '更新專案來源' : '保存新專案'}
            </Button>
            <p>最多 12 筆來源，每題最多 2 筆 Git。更新會讓原草稿失效。</p>
          </fieldset>
        </details>
      </> : null}
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
    </section>
  )
}
