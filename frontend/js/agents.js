const labels = {
  perception: 'Event details',
  risk: 'Safety check',
  routing: 'Safe travel options',
  allocation: 'Relief supplies',
  escalation: 'Human review',
};
const symbols = { perception: '1', risk: '2', routing: '3', allocation: '4', escalation: '5' };

function textNode(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = String(value ?? '');
  return node;
}

function outcomeText(outcome) {
  return ({
    AUTO_APPROVED: 'Checks passed for this simulation',
    PENDING_APPROVAL: 'A person needs to review this suggestion',
    BLOCKED: 'Suggestion stopped by a safety check',
    UNREACHABLE: 'No safe travel option was found',
    PROPOSED: 'A relief suggestion is ready',
    COMPLETED: 'Review complete',
    REJECTED: 'Event details need checking',
    DUPLICATE: 'This event was already entered',
    UNCERTAIN: 'More information is needed',
    ERROR: 'The review could not be completed',
  })[outcome] || 'Review complete';
}

function outcomeSummary(result) {
  if (result.outcome === 'BLOCKED') return 'The suggestion did not pass a safety or supply check. Review the event details before continuing.';
  if (result.outcome === 'PENDING_APPROVAL') return 'A person must review this suggestion. No supplies have moved and no changes have been made.';
  if (result.outcome === 'AUTO_APPROVED') return 'The checks passed for this simulated suggestion. Nothing has been dispatched or changed.';
  if (result.outcome === 'UNREACHABLE') return 'The system could not find a safe way for relief units to reach a shelter. No delivery was suggested.';
  if (result.outcome === 'PROPOSED') {
    const count = result.proposed_actions?.length || 0;
    return `${count} possible relief delivery plan${count === 1 ? '' : 's'} found. Nothing has been dispatched.`;
  }
  if (result.outcome === 'DUPLICATE') return 'This event matches one that was already entered, so it was not reviewed again.';
  if (result.outcome === 'REJECTED') return 'The event details could not be checked. Review the selected event and its information, then try again.';
  if (result.outcome === 'UNCERTAIN') return 'There is not enough reliable information for a confident review. Confirm the details before continuing.';
  if (result.outcome === 'ERROR') return 'A system check could not finish. Try again; if the problem continues, contact the project maintainer.';
  return 'The review finished. No relief delivery was suggested, and the simulation has not changed.';
}

function agentExplanation(key, result) {
  const status = result.agents?.[key]?.status;
  if (status === 'SKIPPED') return 'This check could not run because an earlier check needs attention.';
  if (status === 'ERROR') return 'This check could not be completed. Try the review again.';
  if (key === 'perception') {
    const state = result.perception_result?.status;
    if (state === 'validated') return 'The event details passed the initial checks.';
    if (state === 'duplicate') return 'This event appears to have already been entered.';
    if (state === 'uncertain') return 'Some event details are unclear and need a person to confirm them.';
    return 'The event details need correction before the other checks can run.';
  }
  if (key === 'risk') {
    const level = result.risk_result?.level;
    if (!level || level === 'UNAVAILABLE') return 'A danger level could not be estimated because measured risk information was not provided.';
    const words = { LOW: 'low', MODERATE: 'moderate', HIGH: 'high', CRITICAL: 'very high' };
    const qualifier = result.risk_result.complete ? '' : ' The estimate is partial because some measurements are missing.';
    return `Estimated danger level: ${words[level] || 'unclear'}.${qualifier}`;
  }
  if (key === 'routing') {
    const routes = result.routing_result || [];
    const reachable = routes.filter(route => route.result?.reachable).length;
    if (!routes.length) return 'There were no relief unit and shelter pairs to check.';
    if (!reachable) return 'No safe travel option was found for the available relief units.';
    return `Found ${reachable} possible travel option${reachable === 1 ? '' : 's'} for relief units and shelters.`;
  }
  if (key === 'allocation') {
    const proposals = result.proposed_actions || [];
    const moves = result.allocation_result?.occupant_transfers || [];
    const count = proposals.length + moves.filter(move => move.proposed_quantity > 0).length;
    return count
      ? `Prepared ${count} possible relief plan${count === 1 ? '' : 's'} based on available supplies and shelter space.`
      : 'No relief delivery was suggested from the information available.';
  }
  if (key === 'escalation') {
    const state = result.escalation_result?.status;
    if (state === 'BLOCKED') return 'The suggestion failed a safety or supply check and cannot proceed.';
    if (state === 'PENDING_APPROVAL') return 'A person must review this suggestion before it can be used.';
    if (state === 'AUTO_APPROVED') return 'The suggestion passed the checks for this simulation. No action has been carried out.';
    return 'No suggestion needed a separate review.';
  }
  return 'This check is complete.';
}

function routeForProposal(result, proposal) {
  return (result.routing_result || []).find(route =>
    route.unit_id === proposal.unit_id &&
    route.shelter_id === proposal.shelter_id &&
    route.result?.reachable,
  );
}

export function renderAgentEvaluation(result, twinState, onShowRoute = () => {}, onShowRoutes = null) {
  const panel = document.querySelector('.agent-panel');
  const list = panel.querySelector('.agent-list');
  const status = panel.querySelector('.offline-badge, .agent-status');
  status.className = `agent-status ${result.outcome === 'BLOCKED' ? 'agent-status-warning' : ''}`;
  status.textContent = outcomeText(result.outcome);
  list.replaceChildren();
  for (const key of Object.keys(labels)) {
    const row = document.createElement('div');
    const state = result.agents?.[key]?.status || 'SKIPPED';
    const statusLabel = state === 'SUCCESS' || state === 'COMPLETED' ? 'Done'
      : state === 'PROPOSED' ? 'Suggestion ready'
        : state === 'SKIPPED' ? 'Not run'
          : state === 'ERROR' ? 'Could not finish'
            : state === 'UNCERTAIN' ? 'Needs checking'
              : state === 'UNREACHABLE' ? 'No safe option' : 'Checked';
    row.className = state === 'ERROR' || state === 'UNCERTAIN' || state === 'UNREACHABLE' ? 'agent-row attention' : 'agent-row';
    row.append(textNode('span', 'agent-symbol', symbols[key]), textNode('b', '', labels[key]));
    row.append(textNode('small', 'agent-row-result', statusLabel));
    row.append(textNode('p', 'agent-row-explanation', agentExplanation(key, result)));
    list.append(row);
  }
  panel.querySelector('.agent-result')?.remove();
  const resultBox = document.createElement('div'); resultBox.className = 'agent-result';
  resultBox.append(textNode('span', 'agent-result-kicker', 'WHAT THIS MEANS'));
  resultBox.append(textNode('h3', 'agent-result-title', outcomeText(result.outcome)));
  resultBox.append(textNode('p', 'agent-result-summary', outcomeSummary(result)));

  const proposals = result.proposed_actions || [];
  if (proposals.length) {
    const units = new Map((twinState?.relief_units || []).map(unit => [unit.id, unit]));
    const shelters = new Map((twinState?.shelters || []).map(shelter => [shelter.id, shelter]));
    const routableProposals = proposals.map(proposal => ({ proposal, route: routeForProposal(result, proposal) }))
      .filter(item => item.proposal.route_road_ids?.length && item.route?.result?.route_geometry?.coordinates?.length > 1);
    const heading = textNode('h4', 'agent-section-title', 'Suggested routes');
    resultBox.append(heading);
    resultBox.append(textNode('p', 'agent-route-summary', routableProposals.length
      ? `${routableProposals.length} relief route${routableProposals.length === 1 ? '' : 's'} displayed on the map.`
      : 'No complete route geometry is available to display.'));
    if (routableProposals.length && onShowRoutes) {
      const showButton = textNode('button', 'agent-route-button', 'View routes on map');
      showButton.type = 'button';
      showButton.addEventListener('click', () => {
        onShowRoutes(routableProposals.map(item => item.route));
        document.getElementById('map-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      resultBox.append(showButton);
      onShowRoutes(routableProposals.map(item => item.route));
    } else if (routableProposals.length) {
      const { proposal, route } = routableProposals[0];
      onShowRoute(proposal.route_road_ids, units.get(proposal.unit_id)?.location, route.result.route_geometry.coordinates);
    }
    const details = document.createElement('details');
    details.className = 'agent-delivery-details';
    details.append(textNode('summary', '', `Delivery details (${proposals.length})`));
    const deliveryList = document.createElement('ul');
    for (const proposal of proposals) {
      const unit = units.get(proposal.unit_id);
      const shelter = shelters.get(proposal.shelter_id);
      const unitName = unit?.name || `Relief unit ${proposal.unit_id}`;
      const shelterName = shelter?.name || `Shelter ${proposal.shelter_id}`;
      const supply = proposal.item === 'food'
        ? `${proposal.quantity} food packs`
        : proposal.item === 'water'
          ? `${proposal.quantity} litres of drinking water`
          : `${proposal.quantity} ${proposal.item}`;
      deliveryList.append(textNode('li', '', `${supply} → ${shelterName} · from ${unitName}`));
    }
    details.append(deliveryList);
    resultBox.append(details);
  }
  if (result.requires_approval) {
    resultBox.append(textNode('p', 'approval-required', 'A person must review this suggestion before it can be used.'));
  }
  resultBox.append(textNode('p', 'agent-no-mutation', 'This review did not change the simulation or send supplies.'));
  panel.append(resultBox);
  const heading = panel.querySelector('.panel-header p');
  if (heading) heading.textContent = 'Plain-language summary of the event review';
}

export function resetAgentPanel() {
  const panel = document.querySelector('.agent-panel');
  const list = panel.querySelector('.agent-list');
  const status = panel.querySelector('.agent-status, .offline-badge');
  if (status) { status.className = 'offline-badge'; status.textContent = 'READY'; }
  if (list) {
    list.replaceChildren();
    for (const key of Object.keys(labels)) {
      const row = document.createElement('div');
      row.className = 'agent-row pending';
      row.append(textNode('span', 'agent-symbol', symbols[key]), textNode('b', '', labels[key]));
      row.append(textNode('small', 'agent-row-result', 'Waiting'));
      list.append(row);
    }
  }
  const heading = panel.querySelector('.panel-header p');
  if (heading) heading.textContent = 'Choose an event and get a clear review';
  const title = panel.querySelector('.panel-title h2');
  if (title) title.textContent = 'Decision support';
  const foot = panel.querySelector('.panel-foot');
  if (foot) foot.textContent = 'Choose an event and click “Get clear review”. Suggestions never dispatch supplies.';
  panel.querySelector('.agent-result')?.remove();
}
