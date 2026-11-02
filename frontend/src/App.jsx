import { useEffect, useState } from 'react'
import './App.css'

const API_BASE = '/api'
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const TABS = [
  { id: 'people', label: 'People' },
  { id: 'chores', label: 'Chores' },
  { id: 'limits', label: 'Limits' },
  { id: 'charts', label: 'Charts' },
]

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const data = response.headers.get('content-type')?.includes('application/json')
    ? await response.json()
    : await response.text()
  if (!response.ok) throw new Error(data?.detail ?? data ?? `Request failed (${response.status})`)
  return data
}

function buildPrintGroups(assignments) {
  const groups = new Map()
  assignments.forEach((assignment) => {
    const sectionKey = assignment.section_id ?? assignment.section
    if (!groups.has(sectionKey)) {
      groups.set(sectionKey, {
        name: assignment.section,
        order: assignment.section_order,
        rows: new Map(),
      })
    }
    const group = groups.get(sectionKey)
    const key = JSON.stringify([assignment.chore_id, assignment.time_range])
    const rows = group.rows
    if (!rows.has(key)) {
      rows.set(key, {
        order: assignment.chore_order,
        choreId: assignment.chore_id,
        chore: assignment.chore,
        label: assignment.label || assignment.chore,
        timeRange: assignment.time_range,
        peopleByWeekday: {},
      })
    }
    rows.get(key).peopleByWeekday[assignment.weekday] = assignment.person
  })

  return [...groups.values()]
    .sort((first, second) => first.order - second.order)
    .map((group) => ({
      label: group.name,
      rows: [...group.rows.values()].sort((first, second) => first.order - second.order),
    }))
}

function makeScheduleBlock(id, start_time = '00:06', end_time = '23:00', days = WEEKDAYS) {
  return { id, days: [...days], start_time, end_time }
}

function scheduleBlocksFromChore(chore) {
  const grouped = new Map()
  chore.occurrences.forEach((occurrence) => {
    const key = JSON.stringify(occurrence.time_range)
    if (!grouped.has(key)) {
      const [start_time = '', end_time = ''] = occurrence.time_range?.split('-') ?? []
      grouped.set(key, {
        id: grouped.size + 1,
        days: [],
        start_time,
        end_time,
      })
    }
    grouped.get(key).days.push(occurrence.weekday)
  })
  return [...grouped.values()]
}

function scheduleBlocksToOccurrences(blocks) {
  return blocks.flatMap((block) => {
    if (!block.days.length) throw new Error('Select at least one day for every schedule block.')
    if (Boolean(block.start_time) !== Boolean(block.end_time)) {
      throw new Error('Enter both start and end times, or leave both empty.')
    }
    const time_range = block.start_time ? `${block.start_time}-${block.end_time}` : null
    return block.days.map((weekday) => ({ weekday, time_range }))
  })
}

function DayPicker({ selectedDays, onChange }) {
  const toggleDay = (weekday) => {
    const nextDays = selectedDays.includes(weekday)
      ? selectedDays.filter((day) => day !== weekday)
      : [...selectedDays, weekday]
    onChange(WEEKDAYS.filter((day) => nextDays.includes(day)))
  }

  return (
    <div className="day-picker" role="group" aria-label="Scheduled days">
      {WEEKDAYS.map((weekday) => (
        <button
          aria-pressed={selectedDays.includes(weekday)}
          className="day-toggle"
          key={weekday}
          onClick={() => toggleDay(weekday)}
          title={weekday}
          type="button"
        >
          {weekday.slice(0, 2)}
        </button>
      ))}
    </div>
  )
}

function ScheduleBlocks({ blocks, onChange }) {
  const updateBlock = (id, updates) => onChange(
    blocks.map((block) => block.id === id ? { ...block, ...updates } : block),
  )
  const addBlock = () => {
    const nextId = Math.max(0, ...blocks.map((block) => block.id)) + 1
    onChange([...blocks, makeScheduleBlock(nextId)])
  }

  return (
    <div className="schedule-blocks">
      {blocks.map((block) => (
        <fieldset className={`schedule-block${block.start_time || block.end_time ? '' : ' anytime'}`} key={block.id}>
          <div className="block-heading">
            <legend>Time block</legend>
            {blocks.length > 1 && (
              <button
                aria-label="Remove schedule block"
                className="remove-button"
                onClick={() => onChange(blocks.filter((item) => item.id !== block.id))}
                type="button"
              >×</button>
            )}
          </div>
          <label className="field-label">
            <span>Time</span>
            <select
              value={block.start_time || block.end_time ? 'specific' : 'anytime'}
              onChange={(event) => updateBlock(block.id, event.target.value === 'anytime'
                ? { start_time: '', end_time: '' }
                : { start_time: '00:06', end_time: '23:00' })}
            >
              <option value="anytime">Anytime</option>
              <option value="specific">Specific time</option>
            </select>
          </label>
          {(block.start_time || block.end_time) && <div className="time-fields">
            <label className="field-label">
              <span>Start</span>
              <input
                type="time"
                onChange={(event) => updateBlock(block.id, { start_time: event.target.value })}
                value={block.start_time}
              />
            </label>
            <label className="field-label">
              <span>End</span>
              <input
                type="time"
                onChange={(event) => updateBlock(block.id, { end_time: event.target.value })}
                value={block.end_time}
              />
            </label>
          </div>}
          <DayPicker selectedDays={block.days} onChange={(days) => updateBlock(block.id, { days })} />
        </fieldset>
      ))}
      <button className="subtle-button" onClick={addBlock} type="button">+ Add schedule block</button>
    </div>
  )
}

function App() {
  const [people, setPeople] = useState([])
  const [sections, setSections] = useState([])
  const [chores, setChores] = useState([])
  const [rules, setRules] = useState([])
  const [schedules, setSchedules] = useState([])
  const [schedule, setSchedule] = useState(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [activeTab, setActiveTab] = useState('charts')
  const [personName, setPersonName] = useState('')
  const [choreName, setChoreName] = useState('')
  const [choreLabel, setChoreLabel] = useState('')
  const [sectionName, setSectionName] = useState('')
  const [choreSectionId, setChoreSectionId] = useState('')
  const [choreBlocks, setChoreBlocks] = useState([makeScheduleBlock(1, '00:06', '23:00')])
  const [editingChore, setEditingChore] = useState(null)
  const [editLabel, setEditLabel] = useState('')
  const [editSectionId, setEditSectionId] = useState('')
  const [editBlocks, setEditBlocks] = useState([])
  const [sectionDrafts, setSectionDrafts] = useState({})
  const [draggedSectionId, setDraggedSectionId] = useState(null)
  const [draggedChoreId, setDraggedChoreId] = useState(null)
  const [busyTimesPerson, setBusyTimesPerson] = useState('')
  const [busyTimes, setBusyTimes] = useState([])
  const [busyTimeDraft, setBusyTimeDraft] = useState({ weekday: 'Monday', time_range: '09:00-17:00', label: '' })
  const [rulePerson, setRulePerson] = useState('')
  const [ruleMinimum, setRuleMinimum] = useState('0')
  const [ruleMaximum, setRuleMaximum] = useState('')

  const refresh = async () => {
    const [peopleData, sectionData, choreData, ruleData, scheduleData] = await Promise.all([
      request('/people'), request('/sections'), request('/chores'), request('/rules'), request('/schedules?limit=20'),
    ])
    setPeople(peopleData)
    setSections(sectionData)
    setSectionDrafts(Object.fromEntries(sectionData.map((section) => [section.section_id, {
      name: section.name,
    }])))
    setChores(choreData)
    setRules(ruleData)
    setSchedules(scheduleData)
    setSchedule((current) => current ?? scheduleData[0] ?? null)
    setChoreSectionId((current) => current || String(sectionData[0]?.section_id ?? ''))
  }

  useEffect(() => {
    refresh().catch((error) => setMessage(`API unavailable: ${error.message}`))
  }, [])

  const run = async (action) => {
    setBusy(true)
    setMessage('')
    try { await action() } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }

  const addPerson = (event) => {
    event.preventDefault()
    run(async () => {
      const created = await request('/people', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: personName }),
      })
      setPersonName('')
      await refresh()
      setMessage(`Added ${created.name}`)
    })
  }

  const createSection = (event) => {
    event.preventDefault()
    run(async () => {
      const section = await request('/sections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: sectionName }),
      })
      setSectionName('')
      await refresh()
      setChoreSectionId(String(section.section_id))
      setMessage(`Added section ${section.name}`)
    })
  }

  const editSectionField = (sectionId, field, value) => {
    setSectionDrafts((drafts) => ({
      ...drafts,
      [sectionId]: { ...drafts[sectionId], [field]: value },
    }))
  }

  const saveSection = (event, section) => {
    event.preventDefault()
    run(async () => {
      await request(`/sections/${section.section_id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: sectionDrafts[section.section_id].name,
        }),
      })
      await refresh()
      setMessage('Section updated')
    })
  }

  const reorderSections = (draggedId, targetId) => run(async () => {
    if (draggedId === targetId) return
    const ordered = [...sections].sort((first, second) => first.order_index - second.order_index)
    const fromIndex = ordered.findIndex((section) => section.section_id === draggedId)
    const toIndex = ordered.findIndex((section) => section.section_id === targetId)
    if (fromIndex < 0 || toIndex < 0) return
    const [dragged] = ordered.splice(fromIndex, 1)
    ordered.splice(toIndex, 0, dragged)
    await request('/sections/order', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section_ids: ordered.map((section) => section.section_id) }),
    })
    await refresh()
  })

  const reorderChores = (sectionId, draggedId, targetId, insertAfter) => run(async () => {
    if (draggedId === targetId) return
    const ordered = chores
      .filter((chore) => chore.section_id === sectionId)
      .sort((first, second) => first.order_index - second.order_index)
    const fromIndex = ordered.findIndex((chore) => chore.chore_id === draggedId)
    const targetIndex = ordered.findIndex((chore) => chore.chore_id === targetId)
    if (fromIndex < 0 || targetIndex < 0) return
    const [dragged] = ordered.splice(fromIndex, 1)
    const insertionIndex = targetIndex + (insertAfter ? 1 : 0) - (fromIndex < targetIndex ? 1 : 0)
    ordered.splice(insertionIndex, 0, dragged)
    await request(`/sections/${sectionId}/chores/order`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chore_ids: ordered.map((chore) => chore.chore_id) }),
    })
    await refresh()
  })

  const openBusyTimes = (person) => {
    setBusyTimesPerson(person.name)
    setBusyTimes(person.busy_times)
  }

  const addBusyTime = () => {
    if (!busyTimes.some((block) => block.weekday === busyTimeDraft.weekday && block.time_range === busyTimeDraft.time_range)) {
      setBusyTimes([...busyTimes, busyTimeDraft])
    }
  }

  const saveBusyTimes = (event) => {
    event.preventDefault()
    run(async () => {
      await request(`/people/${encodeURIComponent(busyTimesPerson)}/busy-times`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ blocks: busyTimes }),
      })
      const savedName = busyTimesPerson
      setBusyTimesPerson('')
      await refresh()
      setMessage(`Updated busy times for ${savedName}`)
    })
  }

  const addChore = (event) => {
    event.preventDefault()
    run(async () => {
      const selectedSection = sections.find((section) => String(section.section_id) === choreSectionId)
      if (!selectedSection) throw new Error('Create or select a section first.')
      const created = await request('/chores', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: choreName, label: choreLabel || undefined,
          section_id: selectedSection.section_id,
          occurrences: scheduleBlocksToOccurrences(choreBlocks),
        }),
      })
      setChoreName('')
      setChoreLabel('')
      setChoreBlocks([makeScheduleBlock(1, '00:06', '23:00')])
      await refresh()
      setMessage(`Saved ${created.name}`)
    })
  }

  const openChoreEditor = (chore) => {
    setEditingChore(chore)
    setEditLabel(chore.label)
    setEditSectionId(String(chore.section_id))
    setEditBlocks(scheduleBlocksFromChore(chore))
  }

  const saveChoreEdit = (event) => {
    event.preventDefault()
    run(async () => {
      if (!editingChore) return
      await request(`/chores/${editingChore.chore_id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editingChore.name,
          label: editLabel,
          section_id: Number(editSectionId),
          occurrences: scheduleBlocksToOccurrences(editBlocks),
        }),
      })
      setEditingChore(null)
      await refresh()
      setMessage(`Updated ${editingChore.name}`)
    })
  }

  const deleteChore = (chore) => run(async () => {
    await request(`/chores/${chore.chore_id}`, { method: 'DELETE' })
    await refresh()
    setMessage(`Removed ${chore.label}`)
  })

  const saveRule = (event) => {
    event.preventDefault()
    run(async () => {
      const rule = await request(`/rules/${encodeURIComponent(rulePerson)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minimum: Number(ruleMinimum), maximum: ruleMaximum === '' ? null : Number(ruleMaximum) }),
      })
      await refresh()
      setMessage(`Updated assignment limits for ${rule.person}`)
    })
  }

  const generateSchedule = () => run(async () => {
    const generated = await request('/schedules/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
    })
    setSchedule(generated)
    await refresh()
    setSchedule(generated)
    setMessage(`Generated schedule ${generated.schedule_id}`)
  })

  const selectSchedule = (event) => run(async () => {
    setSchedule(await request(`/schedules/${event.target.value}`))
  })

  const downloadCsv = async () => {
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch(`${API_BASE}/schedules/${schedule.schedule_id}/csv`)
      if (!response.ok) throw new Error((await response.json()).detail ?? 'Unable to download CSV')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = `schedule-${schedule.schedule_id}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }

  const printGroups = buildPrintGroups(schedule?.assignments ?? [])

  return (
    <main className="workspace">
      <header className="masthead">
        <div><p className="eyebrow">HOUSEHOLD / WEEKLY ROTATION</p><h1>Chore chart</h1></div>
        <div className="masthead-meta"><span className="status-dot" /><span>{people.length} {people.length === 1 ? 'person' : 'people'}</span><span className="meta-divider" /><span>{chores.length} {chores.length === 1 ? 'chore' : 'chores'}</span></div>
      </header>

      {message && <div className="notice" role="status">{message}</div>}

      <nav className="tabs" role="tablist" aria-label="Chore chart sections">
        {TABS.map((tab) => (
          <button
            aria-controls={`${tab.id}-panel`}
            aria-selected={activeTab === tab.id}
            className={`tab${activeTab === tab.id ? ' active' : ''}`}
            id={`${tab.id}-tab`}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >{tab.label}</button>
        ))}
      </nav>

      {activeTab === 'people' && <section className="tab-panel" id="people-panel" role="tabpanel" aria-labelledby="people-tab">
        <div className="panel-heading"><div><p className="eyebrow">HOUSEHOLD</p><h2>People</h2></div>
          <form className="inline-form" onSubmit={addPerson}>
            <label className="sr-only" htmlFor="person-name">Person name</label>
            <input id="person-name" value={personName} onChange={(event) => setPersonName(event.target.value)} placeholder="Add a person" required />
            <button className="icon-button add-button" type="submit" disabled={busy} aria-label="Add person" title="Add person">+</button>
          </form>
        </div>
        {people.length ? <ul className="people-list">{people.map((person) => (
          <li key={person.person_id}>
            <span className="person-mark">{person.name.slice(0, 1).toUpperCase()}</span>
            <span className="person-name">{person.name}</span>
            <button className="text-button" type="button" onClick={() => openBusyTimes(person)}>{busyTimesPerson === person.name ? 'Editing busy times' : 'Busy times'}</button>
          </li>
        ))}</ul> : <p className="empty-note">No people yet.</p>}
        {busyTimesPerson && <form className="editor busy-times-editor" onSubmit={saveBusyTimes}>
          <div className="editor-title"><strong>{busyTimesPerson} · Busy times</strong><button type="button" className="close-button" onClick={() => setBusyTimesPerson('')} aria-label="Close busy times editor">×</button></div>
          <div className="slot-list">{busyTimes.map((block, index) => (
            <div className="slot-row" key={`${block.weekday}-${block.time_range}-${index}`}><span>{block.weekday}</span><span>{block.time_range}</span><span>{block.label}</span><button type="button" className="remove-button" onClick={() => setBusyTimes(busyTimes.filter((_, blockIndex) => blockIndex !== index))} aria-label={`Remove ${block.weekday} busy time`}>×</button></div>
          ))}</div>
          <div className="slot-controls">
            <select aria-label="Busy weekday" value={busyTimeDraft.weekday} onChange={(event) => setBusyTimeDraft({ ...busyTimeDraft, weekday: event.target.value })}>{WEEKDAYS.map((day) => <option key={day}>{day}</option>)}</select>
            <input aria-label="Busy time range" value={busyTimeDraft.time_range} onChange={(event) => setBusyTimeDraft({ ...busyTimeDraft, time_range: event.target.value })} placeholder="09:00-17:00" />
            <input aria-label="Busy time label (optional)" value={busyTimeDraft.label} onChange={(event) => setBusyTimeDraft({ ...busyTimeDraft, label: event.target.value })} placeholder="Label (optional)" />
            <button type="button" className="subtle-button" onClick={addBusyTime}>Add block</button>
          </div>
          <button className="save-button" type="submit" disabled={busy}>Save busy times</button>
        </form>}
      </section>}

      {activeTab === 'chores' && <section className="tab-panel" id="chores-panel" role="tabpanel" aria-labelledby="chores-tab">
        <div className="panel-heading"><div><p className="eyebrow">SECTIONS / SCHEDULE</p><h2>Chores</h2></div></div>
        <section className="section-manager">
          <h3>Sections</h3>
          <form className="inline-form" onSubmit={createSection}>
            <label className="sr-only" htmlFor="section-name">New section name</label>
            <input id="section-name" value={sectionName} onChange={(event) => setSectionName(event.target.value)} placeholder="Add a section" required />
            <button className="subtle-button" type="submit" disabled={busy}>Add section</button>
          </form>
          {sections.map((section) => <form
            className="section-edit-row"
            key={section.section_id}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              if (draggedSectionId !== null) reorderSections(draggedSectionId, section.section_id)
              setDraggedSectionId(null)
            }}
            onSubmit={(event) => saveSection(event, section)}
          >
            <button
              aria-label={`Drag to reorder ${section.name}`}
              className="drag-handle"
              draggable="true"
              onDragEnd={() => setDraggedSectionId(null)}
              onDragStart={(event) => {
                setDraggedSectionId(section.section_id)
                event.dataTransfer.effectAllowed = 'move'
                event.dataTransfer.setData('text/plain', String(section.section_id))
              }}
              title="Drag to reorder section"
              type="button"
            >⠿</button>
            <input aria-label={`${section.name} section name`} value={sectionDrafts[section.section_id]?.name ?? section.name} onChange={(event) => editSectionField(section.section_id, 'name', event.target.value)} required />
            <button className="text-button" type="submit" disabled={busy}>Save</button>
          </form>)}
        </section>

        <form className="chore-form" onSubmit={addChore}>
          <h3>New chore</h3>
          <div className="form-grid">
            <label className="field-label"><span>Name</span><input value={choreName} onChange={(event) => setChoreName(event.target.value)} placeholder="e.g. Sweep" required /></label>
            <label className="field-label"><span>Display label <small>optional</small></span><input value={choreLabel} onChange={(event) => setChoreLabel(event.target.value)} placeholder="Defaults to name" /></label>
            <label className="field-label"><span>Section</span><select value={choreSectionId} onChange={(event) => setChoreSectionId(event.target.value)} required><option value="">Choose section</option>{sections.map((section) => <option key={section.section_id} value={section.section_id}>{section.name}</option>)}</select></label>
          </div>
          <ScheduleBlocks blocks={choreBlocks} onChange={setChoreBlocks} />
          <button className="save-button" type="submit" disabled={busy || !sections.length}>Save chore</button>
        </form>

        <div className="chore-table-wrap">
          <table className="data-table">
            <thead><tr><th aria-hidden="true" /><th>Name</th><th>Label</th><th>Days</th><th>Time</th><th><span className="sr-only">Actions</span></th></tr></thead>
            {sections.map((section) => <tbody key={section.section_id}>
              <tr
                className="section-drop-row"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  if (draggedSectionId !== null) reorderSections(draggedSectionId, section.section_id)
                  setDraggedSectionId(null)
                }}
              ><th colSpan="6">{section.name}</th></tr>
              {chores.filter((chore) => chore.section_id === section.section_id).map((chore) => (
                <tr
                  key={chore.chore_id}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault()
                    if (draggedChoreId !== null) {
                      const bounds = event.currentTarget.getBoundingClientRect()
                      reorderChores(section.section_id, draggedChoreId, chore.chore_id, event.clientY > bounds.top + bounds.height / 2)
                    }
                    setDraggedChoreId(null)
                  }}
                >
                  <td><button
                    aria-label={`Drag to reorder ${chore.label}`}
                    className="drag-handle"
                    draggable="true"
                    onDragEnd={() => setDraggedChoreId(null)}
                    onDragStart={(event) => {
                      setDraggedChoreId(chore.chore_id)
                      event.dataTransfer.effectAllowed = 'move'
                      event.dataTransfer.setData('text/plain', String(chore.chore_id))
                    }}
                    title="Drag to reorder chore"
                    type="button"
                  >⠿</button></td>
                  <td>{chore.name}</td><td>{chore.label}</td>
                  <td>{[...new Set(chore.occurrences.map((item) => item.weekday))].map((day) => day.slice(0, 3)).join(', ')}</td>
                  <td>{[...new Set(chore.occurrences.map((item) => item.time_range ?? 'Anytime'))].join(', ')}</td>
                  <td className="table-actions"><button className="text-button" type="button" onClick={() => openChoreEditor(chore)}>Edit</button><button className="remove-button" type="button" onClick={() => deleteChore(chore)} aria-label={`Delete ${chore.label}`} title="Delete chore">×</button></td>
                </tr>
              ))}
            </tbody>)}
          </table>
        </div>
      </section>}

      {activeTab === 'limits' && <section className="tab-panel" id="limits-panel" role="tabpanel" aria-labelledby="limits-tab">
        <div className="panel-heading"><div><p className="eyebrow">FAIR ROTATION</p><h2>Assignment limits</h2></div></div>
        <form className="limit-form" onSubmit={saveRule}>
          <label className="field-label"><span>Person</span><select value={rulePerson} onChange={(event) => setRulePerson(event.target.value)} required><option value="">Choose person</option>{people.map((person) => <option key={person.person_id} value={person.name}>{person.name}</option>)}</select></label>
          <label className="field-label"><span>Minimum</span><input type="number" min="0" value={ruleMinimum} onChange={(event) => setRuleMinimum(event.target.value)} /></label>
          <label className="field-label"><span>Maximum <small>optional</small></span><input type="number" min={ruleMinimum} value={ruleMaximum} onChange={(event) => setRuleMaximum(event.target.value)} placeholder="No limit" /></label>
          <button className="save-button" type="submit" disabled={!people.length || busy}>Save limits</button>
        </form>
        {rules.length > 0 && <ul className="limit-list">{rules.map((rule) => <li key={rule.person}><strong>{rule.person}</strong><span>{rule.minimum} minimum</span><span>{rule.maximum ?? 'No maximum'}</span></li>)}</ul>}
      </section>}

      {activeTab === 'charts' && <section className="tab-panel charts-panel" id="charts-panel" role="tabpanel" aria-labelledby="charts-tab">
        <div className="panel-heading"><div><p className="eyebrow">WEEKLY ASSIGNMENTS</p><h2>Chart</h2></div>
          <div className="schedule-actions">
            <button className="outline-button" type="button" onClick={downloadCsv} disabled={!schedule || busy} title="Download CSV">↓ <span>CSV</span></button>
            <button className="outline-button" type="button" onClick={() => window.print()} disabled={!schedule} title="Print chart">▤ <span>Print</span></button>
            <button className="generate-button" type="button" onClick={generateSchedule} disabled={!people.length || !chores.length || busy}>{busy ? 'Working…' : 'Generate'} <span aria-hidden="true">↗</span></button>
          </div>
        </div>
        <div className="schedule-toolbar">
          <label htmlFor="schedule-history">Saved chart</label>
          <select id="schedule-history" value={schedule?.schedule_id ?? ''} onChange={selectSchedule} disabled={!schedules.length || busy}>
            {!schedules.length && <option value="">No saved charts</option>}
            {schedules.map((item) => <option key={item.schedule_id} value={item.schedule_id}>Chart #{item.schedule_id}{item.created_at ? ` · ${new Date(item.created_at).toLocaleDateString()}` : ''}</option>)}
          </select>
          {schedule && <span className="assignment-count">{schedule.assignments.length} assignments</span>}
        </div>
        {schedule ? <div className="print-schedule">
          <table className="chart-table" aria-label="Weekly chore chart">
            <colgroup><col className="print-chore-column" />{WEEKDAYS.map((weekday) => <col className="print-day-column" key={weekday} />)}</colgroup>
            <thead><tr><th aria-hidden="true" />{WEEKDAYS.map((weekday) => <th scope="col" key={weekday}>{weekday}</th>)}</tr></thead>
            {printGroups.map((group) => <tbody key={group.label}>
              <tr className="print-group"><th scope="rowgroup" colSpan={WEEKDAYS.length + 1}>{group.label}</th></tr>
              {group.rows.map((row) => <tr key={row.choreId}>
                <th scope="row">{row.label}{row.timeRange && <small>{row.timeRange}</small>}</th>
                {WEEKDAYS.map((weekday) => <td key={weekday}>{row.peopleByWeekday[weekday] ?? ''}</td>)}
              </tr>)}
            </tbody>)}
          </table>
        </div> : <div className="schedule-empty"><span className="empty-mark">+</span><h3>No chart yet</h3><p>Add people and chores, then generate a chart.</p></div>}
      </section>}

      {editingChore && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingChore(null) }}>
        <form className="edit-modal" onSubmit={saveChoreEdit} role="dialog" aria-modal="true" aria-labelledby="edit-chore-title">
          <div className="modal-heading"><div><p className="eyebrow">CHORE SETTINGS</p><h2 id="edit-chore-title">Edit {editingChore.name}</h2></div><button className="close-button" type="button" onClick={() => setEditingChore(null)} aria-label="Close dialog">×</button></div>
          <div className="form-grid">
            <label className="field-label"><span>Display label</span><input value={editLabel} onChange={(event) => setEditLabel(event.target.value)} required /></label>
            <label className="field-label"><span>Section</span><select value={editSectionId} onChange={(event) => setEditSectionId(event.target.value)}>{sections.map((section) => <option key={section.section_id} value={section.section_id}>{section.name}</option>)}</select></label>
          </div>
          <ScheduleBlocks blocks={editBlocks} onChange={setEditBlocks} />
          <div className="modal-actions"><button className="subtle-button" type="button" onClick={() => setEditingChore(null)}>Cancel</button><button className="save-button" type="submit" disabled={busy}>Save changes</button></div>
        </form>
      </div>}
    </main>
  )
}

export default App
