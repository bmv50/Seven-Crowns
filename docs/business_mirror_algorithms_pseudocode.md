Business Mirror: ключевые алгоритмы и функции в псевдокоде
Статус: проектный псевдокод по текущей архитектуре.
Дата сборки: 2026-07-07.
Ограничение: язык реализации, физическая СУБД, брокер событий и финальный API-транспорт не утверждены. Псевдокод фиксирует доменную логику, границы функций, входы, выходы, статусы и запреты.
1. Общие типы, статусы и константы
tstype UUID = string
type Timestamp = string
type Money = decimal
type Quantity = decimal
type Percent = decimal
type Json = map<string, any>

type TenantId = UUID
type UserId = UUID
type RoleId = UUID
type PersonId = UUID
type ModelVersionId = UUID
type ObjectId = UUID
type ObjectNodeId = UUID
type OperationId = UUID
type EventId = UUID
type ScenarioId = UUID
type ForecastId = UUID
type MethodVersionId = UUID
type TemplateId = UUID
type LocalInstanceId = UUID

enum Branch {
  PRODUCT_CLIENT_MARKET_B01
  SALES_AND_REVENUE_B02
  BUSINESS_PLANNING_B03
  MAIN_VALUE_CREATION_B06
  PROCUREMENT_AND_SUPPLIERS_B07
  WAREHOUSES_STOCKS_LOGISTICS_B08
  FINANCE_AND_ECONOMICS_B10
  INVESTMENT_ANALYSIS_B11
  HR_AND_ORGANIZATIONAL_SYSTEM_B12
  LEGAL_AND_REGULATORY_CONTOUR_B13
  IT_DATA_AND_DOCUMENTS_B16
  SCENARIO_RISK_CONTOUR_X01
  AI_AND_AGENT_CONTOUR_X02
}

const BRANCH_LABEL: map<Branch, string> = {
  PRODUCT_CLIENT_MARKET_B01: "Продукт, клиент и рынок (B01)",
  SALES_AND_REVENUE_B02: "Продажи и выручка (B02)",
  BUSINESS_PLANNING_B03: "Бизнес-планирование (B03)",
  MAIN_VALUE_CREATION_B06: "Основное создание ценности (B06)",
  PROCUREMENT_AND_SUPPLIERS_B07: "Закупки и поставщики (B07)",
  WAREHOUSES_STOCKS_LOGISTICS_B08: "Склады, запасы и логистика (B08)",
  FINANCE_AND_ECONOMICS_B10: "Финансы и экономика (B10)",
  INVESTMENT_ANALYSIS_B11: "Инвестиционный анализ (B11)",
  HR_AND_ORGANIZATIONAL_SYSTEM_B12: "HR и организационная система (B12)",
  LEGAL_AND_REGULATORY_CONTOUR_B13: "Юридический и регуляторный контур (B13)",
  IT_DATA_AND_DOCUMENTS_B16: "ИТ, данные и документы (B16)",
  SCENARIO_RISK_CONTOUR_X01: "Сквозной сценарно-рисковый контур (X01)",
  AI_AND_AGENT_CONTOUR_X02: "AI и агентный контур (X02)"
}

enum TrustLevel {
  RAW
  ACCEPTED
  NEEDS_CONFIRMATION
  CONFIRMED
  TRUSTED
  CONTRADICTION
  EXCLUDED_FROM_TRUSTED_CALCULATION
}

enum FactTrustStatus {
  DATA_ACCEPTED
  NEEDS_CONFIRMATION
  CONFIRMED
  CONTRADICTION
  MANUAL_CORRECTION
  BACKDATED
  LIKELY_ERROR
  LIKELY_MANIPULATION
  CONFIRMED_MANIPULATION_AFTER_MODERATION
  EXCLUDED_FROM_TRUSTED_CALCULATIONS
}

enum ObjectOperationalStatus {
  WORKS
  WORKS_WITH_LIMITATION
  STOPPED
  IN_TECHNICAL_SERVICE
  IN_REPAIR
  IN_DEFECTATION
  WAITING_SPARE_PARTS
  IN_ACCEPTANCE_AFTER_REPAIR
}

enum EquipmentMaintenanceStatus {
  IN_OPERATION
  IN_RESERVE
  LIMITED_OPERATION
  REQUIRES_OBSERVATION
  REQUIRES_REPAIR
  FORBIDDEN_TO_OPERATE
  IN_REPAIR
  ON_POST_REPAIR_TEST
  ACCEPTED_AFTER_REPAIR
  DECOMMISSIONED
  WRITTEN_OFF
}

enum DefectStatus {
  DETECTED
  REQUIRES_CHECK
  CONFIRMED
  CLASSIFIED
  ALLOWED_FOR_OBSERVATION
  REQUIRES_FIX_IN_CURRENT_REPAIR
  REQUIRES_IMMEDIATE_FIX
  FIXED
  CLOSED_BY_ACCEPTANCE
  REPEATED
  CONVERTED_TO_INCIDENT
}

enum ScenarioStatus {
  WORKING_MODEL
  SCENARIO_COPY
  SCENARIO_ASSUMPTION
  FORECAST_CALCULATION
  APPROVED_CHANGE
  IMPLEMENTATION_FACT
}

enum ModelDepth {
  COMPACT_INPUT_OUTPUT
  PROCESS_OPERATION
  OBJECT_INTERFACE
  OBJECT_NODE
  ELEMENT
  DIRECT_PHYSICAL
  STATISTICAL
  CORRELATION
}

enum ModelApplicability {
  APPLICABLE
  APPLICABLE_WITH_LIMITATION
  NOT_APPLICABLE
  NEEDS_HUMAN_RESTRICTION
  NEEDS_MODEL_DISCLOSURE
}

enum CalculationEffectType {
  AVAILABLE
  AVAILABLE_WITH_LIMIT
  UNAVAILABLE
  REQUIRES_CONTROL
  REQUIRES_REPAIR
  HAS_STOP_THRESHOLD
  HAS_GRAY_ZONE
}

enum CriticalityClass {
  CRITICAL
  IMPORTANT
  RESERVABLE
  EASILY_REPLACEABLE
  RUN_TO_FAILURE_ALLOWED
}

enum RepairStrategy {
  PREVENTIVE_MAINTENANCE
  CONDITION_BASED_REPAIR
  PREDICTIVE_REPAIR
  OBSERVE_WITH_LIMIT
  REPLACE_AT_PLANNED_OPENING
  RUN_TO_FAILURE
  IMMEDIATE_STOP_AND_REPAIR
}

enum BalanceStatus {
  BALANCED
  WITHIN_TOLERANCE
  OUT_OF_TOLERANCE
  IMPOSSIBLE
  NEEDS_EXPLANATION
}

enum SignalClass {
  NOISE
  DATA_ERROR
  MODE_CHANGE
  UNDERDESCRIBED_FACTOR
  DEGRADATION_START
  HIDDEN_DEFECT
  TECHNOLOGY_VIOLATION
  MANIPULATION_ATTEMPT
  NEW_PATTERN
}

enum DisclosureMode {
  INTERNAL_ONLY
  CONTRACTUAL_COUNTERPARTY
  PLATFORM_ANONYMIZED_TRACE
  EXTERNAL_TRUSTED_PROFILE
  REGULATOR
}

type Result<T> = {
  ok: boolean
  value?: T
  errors: ErrorCode[]
  warnings: WarningCode[]
  events: EventId[]
}

type ErrorCode = {
  code: string
  message: string
  target?: string
}

type WarningCode = {
  code: string
  message: string
  target?: string
}
2. Командный контекст, права и доказательность
tstype CommandContext = {
  tenantId: TenantId
  userId: UserId
  roleIds: RoleId[]
  personId?: PersonId
  modelVersionId: ModelVersionId
  requestId: UUID
  timestamp: Timestamp
  source: "user" | "system" | "integration" | "ai_agent" | "offline_client"
  deviceId?: UUID
  ipAddress?: string
  correlationId?: UUID
}

type PermissionRequest = {
  action: "read" | "create" | "update" | "approve" | "execute" | "disclose"
  resourceType: string
  resourceId?: UUID
  branch?: Branch
  objectId?: ObjectId
  operationId?: OperationId
  disclosureMode?: DisclosureMode
}

type Evidence = {
  evidenceId: UUID
  evidenceType: "document" | "measurement" | "photo" | "signature" | "integration_record" | "manual_note"
  sourceId?: UUID
  documentId?: UUID
  hash?: string
  capturedAt: Timestamp
  capturedBy?: UserId
  metadata: Json
}

function requirePermission(ctx: CommandContext, request: PermissionRequest): void {
  let assignments = loadActiveRoleAssignments(ctx.tenantId, ctx.userId, ctx.personId, ctx.timestamp)
  let permissions = loadPermissions(assignments.roleIds)

  let matched = permissions.any(permission =>
    permission.action == request.action &&
    permission.resourceType == request.resourceType &&
    abacConditionsPass(permission.constraints, assignments, request, ctx)
  )

  if (!matched) {
    throw error("ACCESS_DENIED", "Недостаточно полномочий для действия", request.resourceType)
  }
}

function requireDecisionAuthority(
  ctx: CommandContext,
  decisionType: string,
  scope: Json,
  minimumTrustLevel: TrustLevel
): void {
  requirePermission(ctx, {
    action: "approve",
    resourceType: decisionType,
    branch: scope.branch,
    objectId: scope.objectId,
    operationId: scope.operationId
  })

  let roleDecisionRules = loadDecisionRules(ctx.tenantId, ctx.roleIds, decisionType)

  if (!roleDecisionRules.any(rule => rule.scopeCovers(scope) && rule.minTrustLevel <= minimumTrustLevel)) {
    throw error("DECISION_AUTHORITY_MISSING", "Роль не может утвердить это решение", decisionType)
  }
}

function requireEvidenceForSignificantFact(factType: string, evidence: Evidence[]): void {
  let policy = loadEvidencePolicy(factType)

  if (evidence.count < policy.minEvidenceCount) {
    throw error("EVIDENCE_MISSING", "Недостаточно доказательной базы", factType)
  }

  for (let requiredType of policy.requiredEvidenceTypes) {
    if (!evidence.any(item => item.evidenceType == requiredType)) {
      throw error("EVIDENCE_TYPE_MISSING", "Нет обязательного типа доказательства", requiredType)
    }
  }
}
3. Создание модели бизнеса через диалог и документы
tstype BusinessModelDraftInput = {
  tenantId: TenantId
  dialogueAnswers: Json
  uploadedDocuments: UUID[]
  selectedIndustryTemplates: TemplateId[]
  targetBranches: Branch[]
}

type BusinessModelDraft = {
  modelVersionId: ModelVersionId
  tenantId: TenantId
  branchDrafts: map<Branch, Json>
  detectedGaps: ModelGap[]
  grayZones: GrayZone[]
  suggestedNextQuestions: Question[]
  status: "draft" | "needs_human_review" | "ready_for_initial_use"
}

type ModelGap = {
  gapId: UUID
  branch: Branch
  gapType: "missing_owner" | "missing_input" | "missing_output" | "missing_rule" | "missing_evidence" | "missing_integration"
  affectedEntityType: string
  affectedEntityId?: UUID
  severity: "low" | "medium" | "high" | "blocking"
  requiredAction: string
}

type GrayZone = {
  grayZoneId: UUID
  branch: Branch
  reason: string
  affectedScope: Json
  proposedResolutionRole: RoleId[]
}

function createBusinessModelDraftFromDialogue(
  ctx: CommandContext,
  input: BusinessModelDraftInput
): Result<BusinessModelDraft> {
  requirePermission(ctx, { action: "create", resourceType: "business_model_draft" })

  let sourceFacts = extractFactsFromDialogue(input.dialogueAnswers)
  let documentFacts = extractFactsFromDocuments(input.uploadedDocuments)
  let normalizedFacts = normalizeInitialFacts(sourceFacts + documentFacts)

  let templates = loadIndustryTemplates(input.selectedIndustryTemplates)
  let branchDrafts = {}

  for (let branch of input.targetBranches) {
    branchDrafts[branch] = applyBranchTemplates(branch, templates, normalizedFacts)
    branchDrafts[branch] = mapFactsToBranchEntities(branch, branchDrafts[branch], normalizedFacts)
  }

  let crosslinks = buildInitialCrosslinks(branchDrafts)
  let gaps = detectModelGaps(branchDrafts, crosslinks)
  let grayZones = detectGrayZones(branchDrafts, crosslinks, gaps)
  let questions = generateNextQuestions(gaps, grayZones)

  let modelVersion = createModelVersion(ctx, {
    tenantId: input.tenantId,
    status: gaps.hasBlocking() ? "draft" : "initial_draft",
    source: "dialogue_and_documents"
  })

  persistBranchDrafts(modelVersion.id, branchDrafts)
  persistCrosslinks(modelVersion.id, crosslinks)
  persistModelGaps(modelVersion.id, gaps)
  persistGrayZones(modelVersion.id, grayZones)

  let eventId = recordFactEvent(ctx, {
    eventType: "business_model_draft_created",
    payload: { modelVersionId: modelVersion.id, branches: input.targetBranches },
    evidence: buildEvidenceFromInput(input)
  }).value.eventId

  return ok({
    modelVersionId: modelVersion.id,
    tenantId: input.tenantId,
    branchDrafts,
    detectedGaps: gaps,
    grayZones,
    suggestedNextQuestions: questions,
    status: gaps.hasBlocking() ? "needs_human_review" : "ready_for_initial_use"
  }, [eventId])
}

function distributedFillBusinessMirror(
  ctx: CommandContext,
  modelVersionId: ModelVersionId,
  branchAssignments: map<Branch, RoleId[]>
): Result<Task[]> {
  requirePermission(ctx, { action: "update", resourceType: "business_model" })

  let gaps = loadOpenModelGaps(modelVersionId)
  let tasks = []

  for (let gap of gaps) {
    let responsibleRoles = branchAssignments[gap.branch]
    let task = createArmTask(ctx, {
      branch: gap.branch,
      roleIds: responsibleRoles,
      taskType: "fill_model_gap",
      targetEntityType: gap.affectedEntityType,
      targetEntityId: gap.affectedEntityId,
      payload: {
        gapType: gap.gapType,
        requiredAction: gap.requiredAction,
        severity: gap.severity
      }
    })
    tasks.push(task)
  }

  return ok(tasks, tasks.map(task => task.eventId))
}

function detectGrayZones(branchDrafts: map<Branch, Json>, crosslinks: Crosslink[]): GrayZone[] {
  let zones = []

  for (let crosslink of crosslinks) {
    if (crosslink.source.value == null || crosslink.target.value == null) {
      zones.push(grayZone(crosslink.branch, "Нет входа или выхода для стыка", crosslink))
      continue
    }

    if (!crosslink.hasOwner) {
      zones.push(grayZone(crosslink.branch, "Нет владельца подтверждения стыка", crosslink))
      continue
    }

    if (crosslink.valueStatus == "forecast" && crosslink.target.requiresFact) {
      zones.push(grayZone(crosslink.branch, "Прогноз пытается заменить факт", crosslink))
      continue
    }
  }

  return zones
}
4. Библиотека Lego-элементов, шаблоны и локальные экземпляры
tstype LegoTemplate = {
  templateId: TemplateId
  name: string
  templateType: "standard" | "series" | "individual_base" | "customer_specific_service"
  version: string
  constructiveAttributes: Json
  operatingEnvelope: Json
  calculationMethods: MethodVersionId[]
  normativeRanges: UUID[]
  interactionRules: UUID[]
  applicabilityRules: Json
}

type LocalInstance = {
  localInstanceId: LocalInstanceId
  tenantId: TenantId
  templateId: TemplateId
  modelVersionId: ModelVersionId
  localName: string
  serialNumber?: string
  passportAttributes: Json
  localRestrictions: Json
  status: "draft" | "active" | "archived"
}

type IndividualElementRequest = {
  tenantId: TenantId
  candidateName: string
  candidateAttributes: Json
  providedDrawings: UUID[]
  providedCalculationModels: UUID[]
  intendedReuse: "single_customer_unique" | "head_sample_for_series" | "unknown"
}

function shouldCreateIndividualLegoElement(
  request: IndividualElementRequest,
  nearestTemplates: LegoTemplate[]
): {
  createIndividual: boolean
  useNearestTemplate?: TemplateId
  createAsSeriesBase: boolean
  missingInputs: string[]
  reason: string
} {
  if (nearestTemplates.isEmpty()) {
    return {
      createIndividual: true,
      createAsSeriesBase: request.intendedReuse == "head_sample_for_series",
      missingInputs: requiredTechnicalInputsMissing(request),
      reason: "Элемента нет в библиотеке"
    }
  }

  let bestMatch = rankNearestTemplates(request.candidateAttributes, nearestTemplates).first()

  if (bestMatch.similarity >= 0.90 && bestMatch.hasSameConstructiveFamily) {
    return {
      createIndividual: false,
      useNearestTemplate: bestMatch.templateId,
      createAsSeriesBase: false,
      missingInputs: [],
      reason: "Достаточно типового шаблона и локального экземпляра"
    }
  }

  if (bestMatch.similarity >= 0.60 && bestMatch.canBeEditedWithoutBreakingMethodology) {
    return {
      createIndividual: true,
      useNearestTemplate: bestMatch.templateId,
      createAsSeriesBase: request.intendedReuse == "head_sample_for_series",
      missingInputs: requiredTechnicalInputsMissing(request),
      reason: "Берется ближайший конструктив и создается индивидуальный шаблон"
    }
  }

  return {
    createIndividual: true,
    createAsSeriesBase: request.intendedReuse == "head_sample_for_series",
    missingInputs: requiredTechnicalInputsMissing(request),
    reason: "Элемент конструктивно отличается от базы"
  }
}

function createIndividualLegoTemplate(
  ctx: CommandContext,
  request: IndividualElementRequest
): Result<LegoTemplate> {
  requirePermission(ctx, { action: "create", resourceType: "lego_template", branch: Branch.MAIN_VALUE_CREATION_B06 })

  let nearest = findNearestLegoTemplates(request.candidateAttributes)
  let decision = shouldCreateIndividualLegoElement(request, nearest)

  if (!decision.createIndividual) {
    throw error("INDIVIDUAL_TEMPLATE_NOT_REQUIRED", decision.reason, decision.useNearestTemplate)
  }

  if (!decision.missingInputs.isEmpty()) {
    throw error("TECHNICAL_INPUTS_MISSING", "Для индивидуального элемента не хватает исходных данных", decision.missingInputs.join(", "))
  }

  let baseTemplate = decision.useNearestTemplate ? loadTemplate(decision.useNearestTemplate) : emptyTemplate()
  let newTemplate = cloneTemplateForEditing(baseTemplate)

  newTemplate.name = request.candidateName
  newTemplate.templateType = decision.createAsSeriesBase ? "series" : "individual_base"
  newTemplate.constructiveAttributes = normalizeConstructiveAttributes(request.candidateAttributes)
  newTemplate.operatingEnvelope = deriveOperatingEnvelope(request.providedCalculationModels, request.candidateAttributes)
  newTemplate.calculationMethods = bindAllowedCalculationMethods(newTemplate)
  newTemplate.normativeRanges = createNormativeRanges(newTemplate)
  newTemplate.interactionRules = createInteractionRules(newTemplate)
  newTemplate.applicabilityRules = buildApplicabilityRules(newTemplate)

  validateTemplateBeforePublishing(newTemplate)
  persistTemplate(newTemplate)

  let event = appendOnlyEvent(ctx, "lego_template_created", newTemplate.templateId, newTemplate)
  return ok(newTemplate, [event.eventId])
}

function createLocalInstanceFromTemplate(
  ctx: CommandContext,
  templateId: TemplateId,
  localAttributes: Json
): Result<LocalInstance> {
  requirePermission(ctx, { action: "create", resourceType: "local_instance", branch: Branch.MAIN_VALUE_CREATION_B06 })

  let template = loadTemplate(templateId)
  validateTemplateApplicability(template, localAttributes)

  let instance = {
    localInstanceId: uuid(),
    tenantId: ctx.tenantId,
    templateId,
    modelVersionId: ctx.modelVersionId,
    localName: localAttributes.localName,
    serialNumber: localAttributes.serialNumber,
    passportAttributes: pickPassportAttributes(template, localAttributes),
    localRestrictions: extractLocalRestrictions(localAttributes),
    status: "active"
  }

  persistLocalInstance(instance)

  let event = appendOnlyEvent(ctx, "local_instance_created", instance.localInstanceId, instance)
  return ok(instance, [event.eventId])
}

function validateTemplateApplicability(template: LegoTemplate, localAttributes: Json): void {
  let result = evaluateRules(template.applicabilityRules, localAttributes)

  if (!result.ok) {
    throw error("TEMPLATE_NOT_APPLICABLE", "Шаблон не применим к локальному экземпляру", result.reason)
  }

  if (localAttributes.modifiesMethodCriticalFields) {
    throw error("METHODOLOGY_BREAK", "Локальная правка ломает применимость методики", template.templateId)
  }
}
5. Объекты создания ценности, узлы, элементы и интерфейсы
tstype ValueChainObject = {
  objectId: ObjectId
  tenantId: TenantId
  modelVersionId: ModelVersionId
  localInstanceId?: LocalInstanceId
  objectKind:
    | "aggregate"
    | "device"
    | "linear_object"
    | "pipeline"
    | "conveyor"
    | "chute"
    | "flow_switching_element"
    | "flow_mixing_element"
    | "service_device"
    | "control_device"
  name: string
  roleInValueChain: string
  inputParameters: ParameterSpec[]
  outputParameters: ParameterSpec[]
  operatingEnvelope: Json
  criticalityClass: CriticalityClass
  status: ObjectOperationalStatus
}

type ObjectNode = {
  objectNodeId: ObjectNodeId
  objectId: ObjectId
  nodeKind:
    | "constructive_interaction"
    | "element_environment_interaction"
    | "service_interaction"
    | "measurement_interaction"
    | "interface_interaction"
  elements: ElementRef[]
  environment?: EnvironmentRef
  interactionRules: UUID[]
  calculationMethods: MethodVersionId[]
  controlParameters: ParameterSpec[]
  status: EquipmentMaintenanceStatus
}

type InterfaceContour = {
  contourId: UUID
  sourceObjectId: ObjectId
  targetObjectId: ObjectId
  interfaceKind:
    | "material_flow"
    | "energy_flow"
    | "information_signal"
    | "control_signal"
    | "mechanical_connection"
    | "service_supply"
  inputParameters: ParameterSpec[]
  outputParameters: ParameterSpec[]
  transformationRuleId?: UUID
  allowedDeviation: Json
}

type ParameterSpec = {
  code: string
  name: string
  unit?: string
  valueType: "number" | "boolean" | "enum" | "text" | "json"
  plannedValue?: any
  measuredValue?: any
  min?: decimal
  max?: decimal
  tolerance?: decimal
  source: "template" | "local_instance" | "measurement" | "human_restriction" | "calculation" | "scenario"
  trustLevel: TrustLevel
}

type ElementRef = {
  elementId: UUID
  localInstanceId?: LocalInstanceId
  roleInNode: string
}

type EnvironmentRef = {
  environmentKind: "oil" | "water" | "gas" | "air" | "steam" | "dust" | "temperature_field" | "load_field" | "other"
  parameters: ParameterSpec[]
}

function createValueChainObject(
  ctx: CommandContext,
  input: {
    localInstanceId?: LocalInstanceId
    objectKind: ValueChainObject.objectKind
    name: string
    roleInValueChain: string
    inputParameters: ParameterSpec[]
    outputParameters: ParameterSpec[]
    operatingEnvelope: Json
    criticalityClass: CriticalityClass
  }
): Result<ValueChainObject> {
  requirePermission(ctx, { action: "create", resourceType: "value_chain_object", branch: Branch.MAIN_VALUE_CREATION_B06 })

  if (input.inputParameters.isEmpty() || input.outputParameters.isEmpty()) {
    throw error("INPUT_OUTPUT_REQUIRED", "Объект цепочки должен иметь входные и выходные параметры", input.name)
  }

  let object = {
    objectId: uuid(),
    tenantId: ctx.tenantId,
    modelVersionId: ctx.modelVersionId,
    localInstanceId: input.localInstanceId,
    objectKind: input.objectKind,
    name: input.name,
    roleInValueChain: input.roleInValueChain,
    inputParameters: input.inputParameters,
    outputParameters: input.outputParameters,
    operatingEnvelope: input.operatingEnvelope,
    criticalityClass: input.criticalityClass,
    status: ObjectOperationalStatus.WORKS
  }

  persistValueChainObject(object)

  let event = appendOnlyEvent(ctx, "value_chain_object_created", object.objectId, object)
  return ok(object, [event.eventId])
}

function createObjectNode(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    nodeKind: ObjectNode.nodeKind
    elements: ElementRef[]
    environment?: EnvironmentRef
    interactionRules: UUID[]
    calculationMethods: MethodVersionId[]
    controlParameters: ParameterSpec[]
  }
): Result<ObjectNode> {
  requirePermission(ctx, { action: "create", resourceType: "object_node", objectId: input.objectId })

  if (input.elements.isEmpty()) {
    throw error("NODE_ELEMENTS_REQUIRED", "Узел не может существовать без элементов", input.objectId)
  }

  if (input.nodeKind.includes("environment") && input.environment == null) {
    throw error("ENVIRONMENT_REQUIRED", "Для взаимодействия со средой нужна среда", input.objectId)
  }

  let node = {
    objectNodeId: uuid(),
    objectId: input.objectId,
    nodeKind: input.nodeKind,
    elements: input.elements,
    environment: input.environment,
    interactionRules: input.interactionRules,
    calculationMethods: input.calculationMethods,
    controlParameters: input.controlParameters,
    status: EquipmentMaintenanceStatus.IN_OPERATION
  }

  validateNodeIsInsideSingleObject(node)
  persistObjectNode(node)

  let event = appendOnlyEvent(ctx, "object_node_created", node.objectNodeId, node)
  return ok(node, [event.eventId])
}

function validateNodeIsInsideSingleObject(node: ObjectNode): void {
  let objectIds = node.elements.map(element => resolveObjectId(element.elementId)).unique()

  if (objectIds.count > 1 || objectIds.first() != node.objectId) {
    throw error("NODE_OUTSIDE_OBJECT", "Узел существует только внутри одного объекта", node.objectNodeId)
  }
}

function linkInterfaceContour(
  ctx: CommandContext,
  input: {
    sourceObjectId: ObjectId
    targetObjectId: ObjectId
    interfaceKind: InterfaceContour.interfaceKind
    inputParameters: ParameterSpec[]
    outputParameters: ParameterSpec[]
    transformationRuleId?: UUID
    allowedDeviation: Json
  }
): Result<InterfaceContour> {
  requirePermission(ctx, { action: "create", resourceType: "interface_contour", branch: Branch.MAIN_VALUE_CREATION_B06 })

  let source = loadValueChainObject(input.sourceObjectId)
  let target = loadValueChainObject(input.targetObjectId)

  validateParameterCompatibility(source.outputParameters, input.inputParameters)
  validateParameterCompatibility(input.outputParameters, target.inputParameters)

  let contour = {
    contourId: uuid(),
    sourceObjectId: input.sourceObjectId,
    targetObjectId: input.targetObjectId,
    interfaceKind: input.interfaceKind,
    inputParameters: input.inputParameters,
    outputParameters: input.outputParameters,
    transformationRuleId: input.transformationRuleId,
    allowedDeviation: input.allowedDeviation
  }

  persistInterfaceContour(contour)

  let event = appendOnlyEvent(ctx, "interface_contour_created", contour.contourId, contour)
  return ok(contour, [event.eventId])
}

function evaluateObjectInputOutputShell(
  object: ValueChainObject,
  requestedInput: ParameterSpec[],
  targetOutput: ParameterSpec[]
): {
  feasible: boolean
  outputPrediction: ParameterSpec[]
  requiredLimitations: ParameterSpec[]
  reason: string
} {
  let statusEffect = mapStatusToCalculationEffect(object.status)

  if (statusEffect.type == CalculationEffectType.UNAVAILABLE) {
    return {
      feasible: false,
      outputPrediction: [],
      requiredLimitations: statusEffect.limitations,
      reason: "Объект недоступен"
    }
  }

  if (!parametersInsideEnvelope(requestedInput, object.operatingEnvelope)) {
    return {
      feasible: false,
      outputPrediction: [],
      requiredLimitations: buildEnvelopeViolations(requestedInput, object.operatingEnvelope),
      reason: "Входные параметры выходят за допустимую область"
    }
  }

  let outputPrediction = applyInputOutputTransfer(object, requestedInput, statusEffect)
  let outputOk = parametersSatisfyTarget(outputPrediction, targetOutput)

  return {
    feasible: outputOk,
    outputPrediction,
    requiredLimitations: statusEffect.limitations,
    reason: outputOk ? "Входо-выходная оболочка сходится" : "Выходной параметр не достигает цели"
  }
}
6. Статусы доступности и расчетный эффект
tstype AvailabilityInterval = {
  intervalId: UUID
  objectId: ObjectId
  status: ObjectOperationalStatus
  startedAt: Timestamp
  finishedAt?: Timestamp
  productivityCoefficient: decimal
  forbiddenModes: string[]
  limitations: ParameterSpec[]
  trustLevel: TrustLevel
  ownerRoleId: RoleId
  reasonEventId: EventId
}

type StatusCalculationEffect = {
  type: CalculationEffectType
  availabilityCoefficient: decimal
  productivityCoefficient: decimal
  allowedForPlanning: boolean
  forbiddenModes: string[]
  limitations: ParameterSpec[]
  requiresHumanOwner: boolean
  receivers: Branch[]
}

function setAvailabilityInterval(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    status: ObjectOperationalStatus
    startedAt: Timestamp
    finishedAt?: Timestamp
    productivityCoefficient?: decimal
    forbiddenModes?: string[]
    limitations?: ParameterSpec[]
    reasonEventId: EventId
  }
): Result<AvailabilityInterval> {
  requirePermission(ctx, { action: "update", resourceType: "availability_interval", objectId: input.objectId })

  let effect = mapStatusToCalculationEffect(input.status)
  requireDecisionAuthority(ctx, "object_availability_status_change", { objectId: input.objectId, branch: Branch.MAIN_VALUE_CREATION_B06 }, effect.requiresHumanOwner ? TrustLevel.CONFIRMED : TrustLevel.ACCEPTED)

  closePreviousOpenAvailabilityInterval(input.objectId, input.startedAt)

  let interval = {
    intervalId: uuid(),
    objectId: input.objectId,
    status: input.status,
    startedAt: input.startedAt,
    finishedAt: input.finishedAt,
    productivityCoefficient: input.productivityCoefficient ?? effect.productivityCoefficient,
    forbiddenModes: input.forbiddenModes ?? effect.forbiddenModes,
    limitations: input.limitations ?? effect.limitations,
    trustLevel: TrustLevel.CONFIRMED,
    ownerRoleId: selectCurrentDecisionOwner(ctx.roleIds),
    reasonEventId: input.reasonEventId
  }

  persistAvailabilityInterval(interval)

  let event = appendOnlyEvent(ctx, "availability_interval_set", interval.intervalId, interval)
  sendStatusEffectToReceivers(interval, effect.receivers)

  return ok(interval, [event.eventId])
}

function mapStatusToCalculationEffect(status: ObjectOperationalStatus): StatusCalculationEffect {
  switch (status) {
    case ObjectOperationalStatus.WORKS:
      return {
        type: CalculationEffectType.AVAILABLE,
        availabilityCoefficient: 1.0,
        productivityCoefficient: 1.0,
        allowedForPlanning: true,
        forbiddenModes: [],
        limitations: [],
        requiresHumanOwner: false,
        receivers: [
          Branch.BUSINESS_PLANNING_B03,
          Branch.MAIN_VALUE_CREATION_B06,
          Branch.FINANCE_AND_ECONOMICS_B10
        ]
      }

    case ObjectOperationalStatus.WORKS_WITH_LIMITATION:
      return {
        type: CalculationEffectType.AVAILABLE_WITH_LIMIT,
        availabilityCoefficient: 1.0,
        productivityCoefficient: loadCurrentLimitationCoefficient(),
        allowedForPlanning: true,
        forbiddenModes: loadForbiddenModesFromRestriction(),
        limitations: loadCurrentHumanRestrictions(),
        requiresHumanOwner: true,
        receivers: [
          Branch.BUSINESS_PLANNING_B03,
          Branch.MAIN_VALUE_CREATION_B06,
          Branch.SCENARIO_RISK_CONTOUR_X01,
          Branch.FINANCE_AND_ECONOMICS_B10
        ]
      }

    case ObjectOperationalStatus.STOPPED:
      return {
        type: CalculationEffectType.UNAVAILABLE,
        availabilityCoefficient: 0.0,
        productivityCoefficient: 0.0,
        allowedForPlanning: false,
        forbiddenModes: ["all_production_modes"],
        limitations: [],
        requiresHumanOwner: true,
        receivers: [
          Branch.BUSINESS_PLANNING_B03,
          Branch.MAIN_VALUE_CREATION_B06,
          Branch.SCENARIO_RISK_CONTOUR_X01,
          Branch.FINANCE_AND_ECONOMICS_B10
        ]
      }

    case ObjectOperationalStatus.IN_TECHNICAL_SERVICE:
    case ObjectOperationalStatus.IN_REPAIR:
    case ObjectOperationalStatus.IN_DEFECTATION:
    case ObjectOperationalStatus.WAITING_SPARE_PARTS:
    case ObjectOperationalStatus.IN_ACCEPTANCE_AFTER_REPAIR:
      return {
        type: CalculationEffectType.REQUIRES_REPAIR,
        availabilityCoefficient: 0.0,
        productivityCoefficient: 0.0,
        allowedForPlanning: false,
        forbiddenModes: ["production_until_released"],
        limitations: [],
        requiresHumanOwner: true,
        receivers: [
          Branch.MAIN_VALUE_CREATION_B06,
          Branch.PROCUREMENT_AND_SUPPLIERS_B07,
          Branch.WAREHOUSES_STOCKS_LOGISTICS_B08,
          Branch.HR_AND_ORGANIZATIONAL_SYSTEM_B12,
          Branch.FINANCE_AND_ECONOMICS_B10
        ]
      }
  }
}
7. Технологические операции, потоки и балансы
tstype TechnologicalOperation = {
  operationId: OperationId
  tenantId: TenantId
  modelVersionId: ModelVersionId
  processId: UUID
  name: string
  inputParameters: ParameterSpec[]
  outputParameters: ParameterSpec[]
  participatingObjects: ObjectId[]
  requiredRoles: RoleId[]
  requiredMaterials: MaterialRequirement[]
  requiredDocuments: UUID[]
  calculationRuleId?: UUID
  normativeRuleId?: UUID
  allowedDeviation: Json
  status: "draft" | "active" | "blocked" | "archived"
}

type MaterialRequirement = {
  itemId: UUID
  quantity: Quantity
  unit: string
  allowedSubstitutes: UUID[]
}

type OperationExecutionInput = {
  operationId: OperationId
  plannedStart: Timestamp
  plannedFinish: Timestamp
  requestedInputs: ParameterSpec[]
  targetOutputs: ParameterSpec[]
  selectedObjects?: ObjectId[]
}

type OperationExecutionPlan = {
  operationId: OperationId
  feasible: boolean
  participatingObjects: ObjectId[]
  requiredLimitations: ParameterSpec[]
  expectedOutputs: ParameterSpec[]
  requiredMaterials: MaterialRequirement[]
  requiredRoles: RoleId[]
  blockers: ErrorCode[]
  warnings: WarningCode[]
}

type ProcessBalance = {
  balanceId: UUID
  operationId: OperationId
  inputTotal: Json
  outputTotal: Json
  lossTotal: Json
  deviation: Json
  status: BalanceStatus
  explanation?: string
}

function createTechnologicalOperation(
  ctx: CommandContext,
  input: Omit<TechnologicalOperation, "operationId" | "tenantId" | "modelVersionId" | "status">
): Result<TechnologicalOperation> {
  requirePermission(ctx, { action: "create", resourceType: "technological_operation", branch: Branch.MAIN_VALUE_CREATION_B06 })

  if (input.inputParameters.isEmpty() || input.outputParameters.isEmpty()) {
    throw error("OPERATION_IO_REQUIRED", "Операция должна менять вход и иметь выход", input.name)
  }

  if (input.participatingObjects.isEmpty()) {
    throw error("OPERATION_OBJECTS_REQUIRED", "Операция должна быть привязана к объектам", input.name)
  }

  let operation = {
    operationId: uuid(),
    tenantId: ctx.tenantId,
    modelVersionId: ctx.modelVersionId,
    processId: input.processId,
    name: input.name,
    inputParameters: input.inputParameters,
    outputParameters: input.outputParameters,
    participatingObjects: input.participatingObjects,
    requiredRoles: input.requiredRoles,
    requiredMaterials: input.requiredMaterials,
    requiredDocuments: input.requiredDocuments,
    calculationRuleId: input.calculationRuleId,
    normativeRuleId: input.normativeRuleId,
    allowedDeviation: input.allowedDeviation,
    status: "active"
  }

  validateOperationCausality(operation)
  persistTechnologicalOperation(operation)

  let event = appendOnlyEvent(ctx, "technological_operation_created", operation.operationId, operation)
  return ok(operation, [event.eventId])
}

function executeOperation(
  ctx: CommandContext,
  input: OperationExecutionInput
): Result<OperationExecutionPlan> {
  requirePermission(ctx, { action: "execute", resourceType: "technological_operation", operationId: input.operationId })

  let operation = loadTechnologicalOperation(input.operationId)
  let objects = loadObjects(input.selectedObjects ?? operation.participatingObjects)
  let blockers = []
  let warnings = []
  let expectedOutputs = []
  let requiredLimitations = []

  for (let object of objects) {
    let check = validateObjectForPlanning(object, {
      requestedInput: input.requestedInputs,
      targetOutput: input.targetOutputs,
      period: [input.plannedStart, input.plannedFinish],
      operationId: input.operationId
    })

    if (!check.feasible) {
      blockers.push(errorCode("OBJECT_NOT_FEASIBLE", check.reason, object.objectId))
    }

    requiredLimitations.addAll(check.requiredLimitations)
    expectedOutputs.addAll(check.outputPrediction)
    warnings.addAll(check.warnings)
  }

  let materialCheck = checkMaterialsAvailability(ctx.tenantId, operation.requiredMaterials, input.plannedStart)
  let roleCheck = checkRolesAvailability(ctx.tenantId, operation.requiredRoles, input.plannedStart, input.plannedFinish)

  blockers.addAll(materialCheck.blockers)
  blockers.addAll(roleCheck.blockers)

  let plan = {
    operationId: input.operationId,
    feasible: blockers.isEmpty(),
    participatingObjects: objects.map(object => object.objectId),
    requiredLimitations,
    expectedOutputs: mergeOutputPredictions(expectedOutputs),
    requiredMaterials: operation.requiredMaterials,
    requiredRoles: operation.requiredRoles,
    blockers,
    warnings
  }

  appendOnlyEvent(ctx, "operation_execution_evaluated", input.operationId, plan)

  return ok(plan, [])
}

function closeOperationWithFact(
  ctx: CommandContext,
  input: {
    operationId: OperationId
    actualStart: Timestamp
    actualFinish: Timestamp
    actualInputs: ParameterSpec[]
    actualOutputs: ParameterSpec[]
    actualLosses: ParameterSpec[]
    evidence: Evidence[]
  }
): Result<ProcessBalance> {
  requirePermission(ctx, { action: "update", resourceType: "technological_operation", operationId: input.operationId })
  requireEvidenceForSignificantFact("operation_execution_fact", input.evidence)

  let factEvent = recordFactEvent(ctx, {
    eventType: "operation_execution_closed",
    payload: input,
    evidence: input.evidence
  }).value

  let balance = calculateProcessBalance(ctx, {
    operationId: input.operationId,
    inputParameters: input.actualInputs,
    outputParameters: input.actualOutputs,
    lossParameters: input.actualLosses,
    sourceEventId: factEvent.eventId
  }).value

  if (balance.status == BalanceStatus.OUT_OF_TOLERANCE || balance.status == BalanceStatus.IMPOSSIBLE) {
    diagnoseBalanceDeviation(ctx, balance)
  }

  return ok(balance, [factEvent.eventId])
}

function calculateProcessBalance(
  ctx: CommandContext,
  input: {
    operationId: OperationId
    inputParameters: ParameterSpec[]
    outputParameters: ParameterSpec[]
    lossParameters: ParameterSpec[]
    sourceEventId: EventId
  }
): Result<ProcessBalance> {
  let operation = loadTechnologicalOperation(input.operationId)
  let tolerance = loadBalanceTolerance(operation.operationId)

  let inputTotal = aggregateComparableParameters(input.inputParameters)
  let outputTotal = aggregateComparableParameters(input.outputParameters)
  let lossTotal = aggregateComparableParameters(input.lossParameters)
  let deviation = compareBalance(inputTotal, outputTotal, lossTotal)
  let status = classifyBalanceStatus(deviation, tolerance)

  let balance = {
    balanceId: uuid(),
    operationId: input.operationId,
    inputTotal,
    outputTotal,
    lossTotal,
    deviation,
    status,
    explanation: status == BalanceStatus.BALANCED ? "Баланс сходится" : undefined
  }

  persistProcessBalance(balance)
  appendOnlyEvent(ctx, "process_balance_calculated", balance.balanceId, { balance, sourceEventId: input.sourceEventId })

  return ok(balance, [])
}

function diagnoseBalanceDeviation(ctx: CommandContext, balance: ProcessBalance): DiagnosticRoute {
  let operation = loadTechnologicalOperation(balance.operationId)
  let route = {
    routeId: uuid(),
    operationId: balance.operationId,
    checks: []
  }

  route.checks.push(checkDataCompleteness(balance))
  route.checks.push(checkMeasurementTrust(balance))
  route.checks.push(checkModeChange(operation, balance))
  route.checks.push(checkUnaccountedLoss(operation, balance))
  route.checks.push(checkHiddenDefect(operation, balance))
  route.checks.push(checkTechnologyViolation(operation, balance))
  route.checks.push(checkManipulationRisk(operation, balance))

  persistDiagnosticRoute(route)
  appendOnlyEvent(ctx, "balance_deviation_route_created", balance.balanceId, route)

  return route
}
8. Применимость расчетных моделей и раскрытие глубины
tstype PlanningCheckInput = {
  requestedInput: ParameterSpec[]
  targetOutput: ParameterSpec[]
  period: [Timestamp, Timestamp]
  operationId?: OperationId
}

type PlanningCheckResult = {
  feasible: boolean
  modelDepthUsed: ModelDepth
  outputPrediction: ParameterSpec[]
  requiredLimitations: ParameterSpec[]
  warnings: WarningCode[]
  reason: string
}

type ModelDepthDecisionInput = {
  objectId: ObjectId
  nodeId?: ObjectNodeId
  criticalityClass: CriticalityClass
  currentStatus: ObjectOperationalStatus
  recentSignals: Signal[]
  hasDefect: boolean
  hasHumanRestriction: boolean
  requestedModeChange: boolean
  priceOfError: Money
  methodApplicability: ModelApplicability
}

function validateObjectForPlanning(
  object: ValueChainObject,
  input: PlanningCheckInput
): PlanningCheckResult {
  let interval = loadAvailabilityInterval(object.objectId, input.period)
  let statusEffect = mapStatusToCalculationEffect(interval.status)

  if (!statusEffect.allowedForPlanning) {
    return {
      feasible: false,
      modelDepthUsed: ModelDepth.COMPACT_INPUT_OUTPUT,
      outputPrediction: [],
      requiredLimitations: statusEffect.limitations,
      warnings: [],
      reason: "Статус объекта запрещает планирование"
    }
  }

  let depth = selectModelDepth({
    objectId: object.objectId,
    criticalityClass: object.criticalityClass,
    currentStatus: interval.status,
    recentSignals: loadRecentSignals(object.objectId),
    hasDefect: hasOpenDefect(object.objectId),
    hasHumanRestriction: statusEffect.limitations.notEmpty(),
    requestedModeChange: detectsModeChange(object, input),
    priceOfError: estimatePriceOfError(object, input),
    methodApplicability: checkMethodApplicability(object, input)
  })

  if (depth == ModelDepth.COMPACT_INPUT_OUTPUT) {
    let shell = evaluateObjectInputOutputShell(object, input.requestedInput, input.targetOutput)
    return {
      feasible: shell.feasible,
      modelDepthUsed: depth,
      outputPrediction: shell.outputPrediction,
      requiredLimitations: shell.requiredLimitations,
      warnings: [],
      reason: shell.reason
    }
  }

  let disclosed = runDisclosedModel(object, input, depth)

  return {
    feasible: disclosed.feasible,
    modelDepthUsed: depth,
    outputPrediction: disclosed.outputPrediction,
    requiredLimitations: disclosed.limitations,
    warnings: disclosed.warnings,
    reason: disclosed.reason
  }
}

function selectModelDepth(input: ModelDepthDecisionInput): ModelDepth {
  if (input.methodApplicability == ModelApplicability.NOT_APPLICABLE) {
    return ModelDepth.CORRELATION
  }

  if (input.hasDefect || input.hasHumanRestriction) {
    return ModelDepth.OBJECT_NODE
  }

  if (input.recentSignals.any(signal => signal.isWeakSignal || signal.class in [
    SignalClass.DEGRADATION_START,
    SignalClass.HIDDEN_DEFECT,
    SignalClass.TECHNOLOGY_VIOLATION
  ])) {
    return ModelDepth.CORRELATION
  }

  if (input.requestedModeChange) {
    return ModelDepth.OBJECT_INTERFACE
  }

  if (input.criticalityClass == CriticalityClass.CRITICAL && input.priceOfError > loadCriticalPriceThreshold()) {
    return ModelDepth.OBJECT_NODE
  }

  if (input.currentStatus == ObjectOperationalStatus.WORKS) {
    return ModelDepth.COMPACT_INPUT_OUTPUT
  }

  if (input.currentStatus == ObjectOperationalStatus.WORKS_WITH_LIMITATION) {
    return ModelDepth.OBJECT_INTERFACE
  }

  return ModelDepth.COMPACT_INPUT_OUTPUT
}

function runDisclosedModel(
  object: ValueChainObject,
  input: PlanningCheckInput,
  depth: ModelDepth
): {
  feasible: boolean
  outputPrediction: ParameterSpec[]
  limitations: ParameterSpec[]
  warnings: WarningCode[]
  reason: string
} {
  switch (depth) {
    case ModelDepth.OBJECT_INTERFACE:
      return runInterfaceModel(object, input)

    case ModelDepth.OBJECT_NODE:
      return runObjectNodeModels(object, input)

    case ModelDepth.ELEMENT:
      return runElementLevelModels(object, input)

    case ModelDepth.DIRECT_PHYSICAL:
      return runDirectPhysicalModel(object, input)

    case ModelDepth.STATISTICAL:
      return runStatisticalModel(object, input)

    case ModelDepth.CORRELATION:
      return runCorrelationModel(object, input)

    default:
      return runInputOutputModel(object, input)
  }
}

function applyMethodByProblemLevel(
  problem: {
    level:
      | "value_chain"
      | "object"
      | "node_inside_object"
      | "element"
      | "interaction"
      | "background_control_parameter"
    objectId?: ObjectId
    nodeId?: ObjectNodeId
    parameterCode?: string
    onlineDiagnosisCost: Money
    priceOfError: Money
  }
): ModelDepth {
  if (problem.level == "background_control_parameter") {
    return ModelDepth.CORRELATION
  }

  if (problem.level == "interaction") {
    return ModelDepth.OBJECT_NODE
  }

  if (problem.onlineDiagnosisCost > problem.priceOfError && problem.level in ["element", "node_inside_object"]) {
    return ModelDepth.CORRELATION
  }

  if (problem.level == "object" || problem.level == "value_chain") {
    return ModelDepth.COMPACT_INPUT_OUTPUT
  }

  return ModelDepth.OBJECT_NODE
}
9. Компиляция объектной модели в производственную программу
tstype ProductionDemand = {
  demandId: UUID
  productOrServiceId: UUID
  period: [Timestamp, Timestamp]
  targetQuantity: Quantity
  targetQuality: Json
  commercialPromise?: Json
  scenarioId?: ScenarioId
}

type ProductionProgram = {
  programId: UUID
  demandId: UUID
  feasible: boolean
  operations: OperationExecutionPlan[]
  bottlenecks: Bottleneck[]
  requiredRepairs: RepairAction[]
  requiredMaterials: MaterialRequirement[]
  requiredRoles: RoleId[]
  financialTrace: Json
  riskSignals: RiskSignal[]
}

type Bottleneck = {
  bottleneckId: UUID
  bottleneckType: "object" | "operation" | "material" | "role" | "document" | "risk_threshold"
  targetId: UUID
  effect: string
  possibleActions: string[]
}

function compileObjectModelToProductionProgram(
  ctx: CommandContext,
  demand: ProductionDemand
): Result<ProductionProgram> {
  requirePermission(ctx, { action: "create", resourceType: "production_program", branch: Branch.MAIN_VALUE_CREATION_B06 })

  let chainCandidates = findTechnologicalChains(demand.productOrServiceId, ctx.modelVersionId)
  let evaluatedChains = []

  for (let chain of chainCandidates) {
    let operationPlans = []
    let bottlenecks = []

    for (let operation of chain.operations) {
      let execution = executeOperation(ctx, {
        operationId: operation.operationId,
        plannedStart: chain.periodFor(operation),
        plannedFinish: chain.periodFor(operation).finish,
        requestedInputs: chain.inputsFor(operation, demand),
        targetOutputs: chain.outputsFor(operation, demand)
      }).value

      operationPlans.push(execution)

      if (!execution.feasible) {
        bottlenecks.addAll(mapBlockersToBottlenecks(execution.blockers))
      }
    }

    let materialCheck = aggregateMaterialRequirements(operationPlans)
    let roleCheck = aggregateRoleRequirements(operationPlans)
    let riskCheck = checkRiskThresholds(chain, demand, operationPlans)
    let repairNeeds = deriveRepairNeedsFromObjectStatuses(operationPlans)

    bottlenecks.addAll(materialCheck.bottlenecks)
    bottlenecks.addAll(roleCheck.bottlenecks)
    bottlenecks.addAll(riskCheck.bottlenecks)

    evaluatedChains.push({
      chainId: chain.chainId,
      feasible: bottlenecks.isEmpty(),
      operationPlans,
      bottlenecks,
      materialCheck,
      roleCheck,
      riskCheck,
      repairNeeds
    })
  }

  let selected = selectBestFeasibleChain(evaluatedChains)

  if (selected == null) {
    selected = selectLeastBadChainForExplanation(evaluatedChains)
  }

  let program = {
    programId: uuid(),
    demandId: demand.demandId,
    feasible: selected.feasible,
    operations: selected.operationPlans,
    bottlenecks: selected.bottlenecks,
    requiredRepairs: selected.repairNeeds,
    requiredMaterials: selected.materialCheck.requirements,
    requiredRoles: selected.roleCheck.roles,
    financialTrace: calculateProgramFinancialTrace(selected, demand),
    riskSignals: selected.riskCheck.signals
  }

  persistProductionProgram(program)
  appendOnlyEvent(ctx, "production_program_compiled", program.programId, program)

  sendProductionProgramOutputs(ctx, program)

  return ok(program, [])
}

function sendProductionProgramOutputs(ctx: CommandContext, program: ProductionProgram): void {
  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.BUSINESS_PLANNING_B03,
    payloadType: "production_capacity_and_bottlenecks",
    payload: {
      programId: program.programId,
      feasible: program.feasible,
      bottlenecks: program.bottlenecks,
      operations: compactOperationPlan(program.operations)
    },
    detailLevel: "planning"
  })

  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.FINANCE_AND_ECONOMICS_B10,
    payloadType: "production_financial_trace",
    payload: program.financialTrace,
    detailLevel: "cost_and_cash_effect"
  })

  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.PROCUREMENT_AND_SUPPLIERS_B07,
    payloadType: "procurement_requirements",
    payload: program.requiredMaterials,
    detailLevel: "nomenclature_quantity_deadline"
  })

  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.WAREHOUSES_STOCKS_LOGISTICS_B08,
    payloadType: "stock_and_movement_requirements",
    payload: program.requiredMaterials,
    detailLevel: "warehouse_position_reservation"
  })

  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.HR_AND_ORGANIZATIONAL_SYSTEM_B12,
    payloadType: "role_and_shift_requirements",
    payload: program.requiredRoles,
    detailLevel: "role_competence_admission_shift"
  })

  selectiveExchange(ctx, {
    sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
    targetBranch: Branch.SCENARIO_RISK_CONTOUR_X01,
    payloadType: "risk_signals",
    payload: program.riskSignals,
    detailLevel: "risk_indicator_owner_threshold"
  })
}
10. Индикативные параметры ТОиР и выбор ремонтной стратегии
tstype RepairDecisionInput = {
  objectId: ObjectId
  nodeId?: ObjectNodeId
  criticalityClass: CriticalityClass
  calculatedResourceRatio?: decimal
  statisticalResourceRatio?: decimal
  regulatoryDeadlineRatio?: decimal
  controlParameterRatio?: decimal
  riskScenarioRatio?: decimal
  defectStatus?: DefectStatus
  replacementCost: Money
  failureCost: Money
  sparePartsAvailable: boolean
  nextPlannedStop?: Timestamp
}

type RepairDecision = {
  strategy: RepairStrategy
  trigger: string
  triggerRatio?: decimal
  requiredActions: string[]
  temporaryLimitations: ParameterSpec[]
  controlVolume: Json
  needsHumanApproval: boolean
}

function chooseRepairStrategy(input: RepairDecisionInput): RepairDecision {
  let triggers = compact([
    ratioTrigger("calculated_resource", input.calculatedResourceRatio),
    ratioTrigger("statistical_resource", input.statisticalResourceRatio),
    ratioTrigger("regulatory_deadline", input.regulatoryDeadlineRatio),
    ratioTrigger("control_parameter", input.controlParameterRatio),
    ratioTrigger("risk_scenario", input.riskScenarioRatio)
  ]).sortBy(trigger => trigger.ratio)

  let earliest = triggers.first()

  if (input.defectStatus == DefectStatus.REQUIRES_IMMEDIATE_FIX) {
    return {
      strategy: RepairStrategy.IMMEDIATE_STOP_AND_REPAIR,
      trigger: "confirmed_critical_defect",
      requiredActions: ["stop_object", "diagnose", "prepare_repair", "approve_repair"],
      temporaryLimitations: [],
      controlVolume: fullControlVolume(input.objectId, input.nodeId),
      needsHumanApproval: true
    }
  }

  if (input.criticalityClass == CriticalityClass.CRITICAL) {
    if (earliest != null && earliest.ratio >= 0.90 && earliest.ratio <= 0.95) {
      return {
        strategy: RepairStrategy.PREVENTIVE_MAINTENANCE,
        trigger: earliest.name,
        triggerRatio: earliest.ratio,
        requiredActions: ["prepare_spare_parts", "plan_preventive_repair", "reserve_window"],
        temporaryLimitations: deriveLimitationsUntilRepair(input),
        controlVolume: increasedControlVolume(input.objectId, input.nodeId),
        needsHumanApproval: true
      }
    }

    return {
      strategy: RepairStrategy.PREDICTIVE_REPAIR,
      trigger: earliest?.name ?? "criticality_without_resource_trigger",
      triggerRatio: earliest?.ratio,
      requiredActions: ["keep_forecast", "increase_control_if_signal_changes"],
      temporaryLimitations: [],
      controlVolume: normalControlVolume(input.objectId, input.nodeId),
      needsHumanApproval: false
    }
  }

  if (input.criticalityClass in [CriticalityClass.IMPORTANT, CriticalityClass.RESERVABLE]) {
    if (earliest != null && earliest.ratio >= 1.00) {
      return {
        strategy: RepairStrategy.CONDITION_BASED_REPAIR,
        trigger: earliest.name,
        triggerRatio: earliest.ratio,
        requiredActions: ["diagnose", "repair_by_condition", "use_next_planned_stop_if_allowed"],
        temporaryLimitations: deriveLimitationsUntilRepair(input),
        controlVolume: increasedControlVolume(input.objectId, input.nodeId),
        needsHumanApproval: true
      }
    }

    return {
      strategy: RepairStrategy.REPLACE_AT_PLANNED_OPENING,
      trigger: "next_planned_opening",
      requiredActions: ["prepare_spare_parts_if_needed", "observe"],
      temporaryLimitations: [],
      controlVolume: normalControlVolume(input.objectId, input.nodeId),
      needsHumanApproval: false
    }
  }

  if (input.criticalityClass == CriticalityClass.RUN_TO_FAILURE_ALLOWED && input.failureCost <= input.replacementCost) {
    return {
      strategy: RepairStrategy.RUN_TO_FAILURE,
      trigger: "failure_cost_allowed",
      requiredActions: ["observe_basic_status", "replace_after_failure"],
      temporaryLimitations: [],
      controlVolume: minimalControlVolume(input.objectId, input.nodeId),
      needsHumanApproval: false
    }
  }

  return {
    strategy: RepairStrategy.CONDITION_BASED_REPAIR,
    trigger: earliest?.name ?? "default_condition_based",
    triggerRatio: earliest?.ratio,
    requiredActions: ["observe", "diagnose_if_signal_worsens"],
    temporaryLimitations: [],
    controlVolume: normalControlVolume(input.objectId, input.nodeId),
    needsHumanApproval: false
  }
}

function createRepairAction(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    nodeId?: ObjectNodeId
    defectId?: UUID
    decision: RepairDecision
    plannedWindow: [Timestamp, Timestamp]
    requiredParts: MaterialRequirement[]
    requiredRoles: RoleId[]
  }
): Result<RepairAction> {
  requirePermission(ctx, { action: "create", resourceType: "repair_action", objectId: input.objectId })

  if (input.decision.needsHumanApproval) {
    requireDecisionAuthority(ctx, "repair_decision", { objectId: input.objectId, branch: Branch.MAIN_VALUE_CREATION_B06 }, TrustLevel.CONFIRMED)
  }

  let stock = checkMaterialsAvailability(ctx.tenantId, input.requiredParts, input.plannedWindow[0])
  if (!stock.ok) {
    createProcurementRequirements(ctx, input.requiredParts, input.plannedWindow[0])
  }

  let action = {
    repairActionId: uuid(),
    objectId: input.objectId,
    nodeId: input.nodeId,
    defectId: input.defectId,
    strategy: input.decision.strategy,
    plannedWindow: input.plannedWindow,
    requiredParts: input.requiredParts,
    requiredRoles: input.requiredRoles,
    status: "planned",
    controlVolume: input.decision.controlVolume
  }

  persistRepairAction(action)
  appendOnlyEvent(ctx, "repair_action_created", action.repairActionId, action)

  return ok(action, [])
}
11. Дефектация, диагностика, ремонт и обратная сборка
tstype Defect = {
  defectId: UUID
  objectId: ObjectId
  nodeId?: ObjectNodeId
  manifestationEventId: EventId
  status: DefectStatus
  severity: "low" | "medium" | "high" | "critical"
  allowedOperation?: Json
  diagnosticRouteId?: UUID
  confirmedCause?: string
}

type DiagnosticRoute = {
  routeId: UUID
  operationId?: OperationId
  objectId?: ObjectId
  nodeId?: ObjectNodeId
  checks: DiagnosticCheck[]
}

type DiagnosticCheck = {
  checkId: UUID
  checkType: string
  targetType: "object" | "node" | "element" | "interaction" | "environment" | "data" | "document"
  targetId?: UUID
  requiredEvidenceTypes: string[]
  status: "planned" | "done" | "skipped" | "failed"
}

type TechnicalReferencePoint = {
  referencePointId: UUID
  objectId: ObjectId
  nodeId?: ObjectNodeId
  createdAt: Timestamp
  sourceRepairActionId?: UUID
  parameters: ParameterSpec[]
  resourceState: Json
  trustLevel: TrustLevel
}

function createDefectFromManifestation(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    nodeId?: ObjectNodeId
    manifestationEventId: EventId
    symptoms: Json
    evidence: Evidence[]
  }
): Result<Defect> {
  requirePermission(ctx, { action: "create", resourceType: "defect", objectId: input.objectId })

  let severity = classifyManifestationSeverity(input.symptoms, input.objectId, input.nodeId)
  let allowedOperation = evaluateAllowedOperationAfterManifestation(input.objectId, input.nodeId, severity)

  let defect = {
    defectId: uuid(),
    objectId: input.objectId,
    nodeId: input.nodeId,
    manifestationEventId: input.manifestationEventId,
    status: severity == "critical" ? DefectStatus.REQUIRES_IMMEDIATE_FIX : DefectStatus.REQUIRES_CHECK,
    severity,
    allowedOperation
  }

  persistDefect(defect)

  if (severity == "critical") {
    setAvailabilityInterval(ctx, {
      objectId: input.objectId,
      status: ObjectOperationalStatus.STOPPED,
      startedAt: ctx.timestamp,
      reasonEventId: input.manifestationEventId
    })
  }

  let route = planDiagnosticRoute(ctx, defect)
  defect.diagnosticRouteId = route.routeId
  updateDefect(defect)

  appendOnlyEvent(ctx, "defect_created", defect.defectId, { defect, evidence: input.evidence })

  return ok(defect, [])
}

function planDiagnosticRoute(ctx: CommandContext, defect: Defect): DiagnosticRoute {
  let object = loadValueChainObject(defect.objectId)
  let node = defect.nodeId ? loadObjectNode(defect.nodeId) : null
  let symptoms = loadManifestation(defect.manifestationEventId)

  let checks = []

  checks.push({
    checkId: uuid(),
    checkType: "verify_source_data",
    targetType: "data",
    requiredEvidenceTypes: ["measurement", "integration_record"],
    status: "planned"
  })

  if (node != null) {
    checks.addAll(buildNodeInteractionChecks(node, symptoms))
    checks.addAll(buildEnvironmentInteractionChecks(node, symptoms))
  }

  checks.addAll(buildObjectEnvelopeChecks(object, symptoms))
  checks.addAll(buildPostSafetyChecks(object, defect.severity))

  let route = {
    routeId: uuid(),
    objectId: defect.objectId,
    nodeId: defect.nodeId,
    checks
  }

  persistDiagnosticRoute(route)
  appendOnlyEvent(ctx, "diagnostic_route_created", route.routeId, route)

  return route
}

function closeDiagnosticRoute(
  ctx: CommandContext,
  routeId: UUID,
  results: DiagnosticCheckResult[]
): Result<Defect> {
  requirePermission(ctx, { action: "update", resourceType: "diagnostic_route" })

  let route = loadDiagnosticRoute(routeId)
  let defect = loadDefectByRoute(routeId)

  validateDiagnosticResults(route, results)

  let confirmedCause = classifyDefectCause(results)
  defect.confirmedCause = confirmedCause
  defect.status = classifyDefectStatusAfterDiagnostics(defect, results)

  updateDefect(defect)
  appendOnlyEvent(ctx, "diagnostic_route_closed", routeId, { results, defect })

  return ok(defect, [])
}

function closeRepairOperation(
  ctx: CommandContext,
  input: {
    repairActionId: UUID
    actualPartsUsed: MaterialRequirement[]
    actualRoles: RoleId[]
    operationResults: Json
    postRepairMeasurements: ParameterSpec[]
    evidence: Evidence[]
  }
): Result<TechnicalReferencePoint> {
  requirePermission(ctx, { action: "update", resourceType: "repair_action" })
  requireEvidenceForSignificantFact("repair_operation_result", input.evidence)

  let action = loadRepairAction(input.repairActionId)
  let acceptance = runPostRepairAcceptance(action, input.postRepairMeasurements)

  if (!acceptance.accepted) {
    appendOnlyEvent(ctx, "repair_acceptance_failed", input.repairActionId, { input, acceptance })
    throw error("REPAIR_ACCEPTANCE_FAILED", "Контроль после ремонта не пройден", input.repairActionId)
  }

  markRepairActionAccepted(action, input)
  closeDefectIfFixed(action.defectId, ctx)

  let referencePoint = updateReferencePointAfterRepair(ctx, {
    objectId: action.objectId,
    nodeId: action.nodeId,
    repairActionId: action.repairActionId,
    postRepairMeasurements: input.postRepairMeasurements,
    evidence: input.evidence
  }).value

  setAvailabilityInterval(ctx, {
    objectId: action.objectId,
    status: ObjectOperationalStatus.WORKS,
    startedAt: ctx.timestamp,
    reasonEventId: appendOnlyEvent(ctx, "repair_action_closed", action.repairActionId, input).eventId
  })

  return ok(referencePoint, [])
}

function updateReferencePointAfterRepair(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    nodeId?: ObjectNodeId
    repairActionId: UUID
    postRepairMeasurements: ParameterSpec[]
    evidence: Evidence[]
  }
): Result<TechnicalReferencePoint> {
  let resourceState = recalculateResourceState(input.objectId, input.nodeId, input.postRepairMeasurements)

  let referencePoint = {
    referencePointId: uuid(),
    objectId: input.objectId,
    nodeId: input.nodeId,
    createdAt: ctx.timestamp,
    sourceRepairActionId: input.repairActionId,
    parameters: input.postRepairMeasurements,
    resourceState,
    trustLevel: calculateMeasurementTrust(input.postRepairMeasurements, input.evidence)
  }

  persistTechnicalReferencePoint(referencePoint)
  appendOnlyEvent(ctx, "technical_reference_point_created", referencePoint.referencePointId, referencePoint)

  recalculateRiskAndProgramAfterReferencePoint(ctx, referencePoint)

  return ok(referencePoint, [])
}
12. Корреляции, нормальное поведение и слабые сигналы
tstype Signal = {
  signalId: UUID
  objectId?: ObjectId
  nodeId?: ObjectNodeId
  parameterCode: string
  observedAt: Timestamp
  value: any
  expectedRange: Json
  deviationScore: decimal
  class?: SignalClass
  isWeakSignal: boolean
  trustLevel: TrustLevel
}

type NormalBehaviorProfile = {
  profileId: UUID
  objectId: ObjectId
  nodeId?: ObjectNodeId
  modeCode: string
  parameterCodes: string[]
  compactCorrelationModel: Json
  confidenceInterval: Json
  validFrom: Timestamp
  validTo?: Timestamp
}

function storeNormalCorrelationCompactly(
  ctx: CommandContext,
  input: {
    objectId: ObjectId
    nodeId?: ObjectNodeId
    modeCode: string
    observations: Measurement[]
    parameterCodes: string[]
  }
): Result<NormalBehaviorProfile> {
  requirePermission(ctx, { action: "create", resourceType: "normal_behavior_profile", objectId: input.objectId })

  let cleaned = filterTrustedMeasurements(input.observations)
  let model = fitCompactCorrelationModel(cleaned, input.parameterCodes)
  let interval = calculateConfidenceInterval(model, cleaned)

  let profile = {
    profileId: uuid(),
    objectId: input.objectId,
    nodeId: input.nodeId,
    modeCode: input.modeCode,
    parameterCodes: input.parameterCodes,
    compactCorrelationModel: serializeCompactModel(model),
    confidenceInterval: interval,
    validFrom: ctx.timestamp
  }

  persistNormalBehaviorProfile(profile)
  appendOnlyEvent(ctx, "normal_behavior_profile_created", profile.profileId, profile)

  return ok(profile, [])
}

function detectWeakSignal(
  ctx: CommandContext,
  measurement: Measurement
): Result<Signal> {
  let profile = loadActiveNormalBehaviorProfile(measurement.objectId, measurement.nodeId, measurement.modeCode)
  let expected = predictExpectedRange(profile, measurement.contextParameters)
  let deviationScore = calculateDeviationScore(measurement.value, expected)

  let classification = classifyCorrelationBreak({
    measurement,
    expected,
    deviationScore,
    profile
  })

  let isWeakSignal =
    classification.class not in [SignalClass.NOISE, SignalClass.DATA_ERROR] &&
    deviationScore >= loadWeakSignalThreshold(measurement.parameterCode) &&
    isRepeatedOrSynchronousOrBusinessRelevant(measurement)

  let signal = {
    signalId: uuid(),
    objectId: measurement.objectId,
    nodeId: measurement.nodeId,
    parameterCode: measurement.parameterCode,
    observedAt: measurement.measuredAt,
    value: measurement.value,
    expectedRange: expected,
    deviationScore,
    class: classification.class,
    isWeakSignal,
    trustLevel: classification.trustLevel
  }

  persistSignal(signal)
  appendOnlyEvent(ctx, "weak_signal_evaluated", signal.signalId, signal)

  if (isWeakSignal) {
    routeWeakSignal(ctx, signal)
  }

  return ok(signal, [])
}

function classifyCorrelationBreak(input: {
  measurement: Measurement
  expected: Json
  deviationScore: decimal
  profile: NormalBehaviorProfile
}): { class: SignalClass, trustLevel: TrustLevel } {
  if (measurementTrustIsLow(input.measurement)) {
    return { class: SignalClass.DATA_ERROR, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
  }

  if (input.deviationScore < loadNoiseThreshold(input.measurement.parameterCode)) {
    return { class: SignalClass.NOISE, trustLevel: TrustLevel.ACCEPTED }
  }

  if (modeChanged(input.measurement, input.profile.modeCode)) {
    return { class: SignalClass.MODE_CHANGE, trustLevel: TrustLevel.ACCEPTED }
  }

  if (hasKnownUnmodeledFactor(input.measurement)) {
    return { class: SignalClass.UNDERDESCRIBED_FACTOR, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
  }

  if (deviationTrendAccelerates(input.measurement)) {
    return { class: SignalClass.DEGRADATION_START, trustLevel: TrustLevel.CONFIRMED }
  }

  if (linkedToDefectSymptoms(input.measurement)) {
    return { class: SignalClass.HIDDEN_DEFECT, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
  }

  if (linkedToTechnologyDeviation(input.measurement)) {
    return { class: SignalClass.TECHNOLOGY_VIOLATION, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
  }

  if (looksLikeManipulation(input.measurement)) {
    return { class: SignalClass.MANIPULATION_ATTEMPT, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
  }

  return { class: SignalClass.NEW_PATTERN, trustLevel: TrustLevel.NEEDS_CONFIRMATION }
}

function routeWeakSignal(ctx: CommandContext, signal: Signal): void {
  if (signal.class == SignalClass.DEGRADATION_START || signal.class == SignalClass.HIDDEN_DEFECT) {
    createDefectFromManifestation(ctx, {
      objectId: signal.objectId,
      nodeId: signal.nodeId,
      manifestationEventId: appendOnlyEvent(ctx, "weak_signal_manifestation", signal.signalId, signal).eventId,
      symptoms: signal,
      evidence: []
    })
  }

  if (signal.class == SignalClass.TECHNOLOGY_VIOLATION) {
    createArmTask(ctx, {
      branch: Branch.MAIN_VALUE_CREATION_B06,
      taskType: "check_technology_violation",
      targetEntityType: "signal",
      targetEntityId: signal.signalId
    })
  }

  if (signal.class == SignalClass.MANIPULATION_ATTEMPT) {
    selectiveExchange(ctx, {
      sourceBranch: Branch.MAIN_VALUE_CREATION_B06,
      targetBranch: Branch.IT_DATA_AND_DOCUMENTS_B16,
      payloadType: "data_trust_incident_candidate",
      payload: signal,
      detailLevel: "evidence_and_trace"
    })
  }
}
13. Качество поставщика, входной и фоновый контроль
tstype SupplierQualitySignal = {
  supplierId: UUID
  itemTemplateId: TemplateId
  period: [Timestamp, Timestamp]
  defectRate: decimal
  earlyFailureRate: decimal
  deviationFromPeerGroup: decimal
  trustLevel: TrustLevel
  recommendedControlLevel: "normal" | "increased" | "full_incoming" | "background_monitoring"
}

function updateSupplierQualitySignal(
  ctx: CommandContext,
  input: {
    supplierId: UUID
    itemTemplateId: TemplateId
    installedPartEvents: EventId[]
    defectEvents: EventId[]
    operatingHours: Quantity
  }
): Result<SupplierQualitySignal> {
  requirePermission(ctx, { action: "update", resourceType: "supplier_quality_signal", branch: Branch.PROCUREMENT_AND_SUPPLIERS_B07 })

  let defectRate = calculateDefectRate(input.defectEvents, input.installedPartEvents)
  let earlyFailureRate = calculateEarlyFailureRate(input.defectEvents, input.operatingHours)
  let peerDeviation = compareSupplierToPeerGroup(input.supplierId, input.itemTemplateId, defectRate, earlyFailureRate)
  let trust = calculateSupplierSignalTrust(input)

  let recommendation = recommendIncomingAndBackgroundControl({
    defectRate,
    earlyFailureRate,
    peerDeviation,
    trust
  })

  let signal = {
    supplierId: input.supplierId,
    itemTemplateId: input.itemTemplateId,
    period: deriveSignalPeriod(input.installedPartEvents, input.defectEvents),
    defectRate,
    earlyFailureRate,
    deviationFromPeerGroup: peerDeviation,
    trustLevel: trust,
    recommendedControlLevel: recommendation
  }

  persistSupplierQualitySignal(signal)
  appendOnlyEvent(ctx, "supplier_quality_signal_updated", input.supplierId, signal)

  return ok(signal, [])
}

function recommendIncomingAndBackgroundControl(input: {
  defectRate: decimal
  earlyFailureRate: decimal
  peerDeviation: decimal
  trust: TrustLevel
}): SupplierQualitySignal.recommendedControlLevel {
  if (input.trust < TrustLevel.CONFIRMED) {
    return "background_monitoring"
  }

  if (input.peerDeviation >= 3.0 || input.earlyFailureRate >= loadCriticalEarlyFailureThreshold()) {
    return "full_incoming"
  }

  if (input.peerDeviation >= 2.0 || input.defectRate >= loadIncreasedControlThreshold()) {
    return "increased"
  }

  return "normal"
}
14. Сценарная копия, прогноз и перевод в рабочую модель
tstype ScenarioCopy = {
  scenarioId: ScenarioId
  tenantId: TenantId
  baseModelVersionId: ModelVersionId
  scenarioModelVersionId: ModelVersionId
  name: string
  ownerRoleId: RoleId
  status: ScenarioStatus
}

type ScenarioAssumption = {
  assumptionId: UUID
  scenarioId: ScenarioId
  targetEntityType: string
  targetEntityId: UUID
  assumptionType: string
  value: Json
  source: "human" | "market" | "calculation" | "document" | "ai_agent"
  trustLevel: TrustLevel
}

type ScenarioForecast = {
  forecastId: ForecastId
  scenarioId: ScenarioId
  affectedBranches: Branch[]
  calculatedAt: Timestamp
  outputs: Json
  blockers: ErrorCode[]
  risks: RiskSignal[]
  status: ScenarioStatus
}

function createScenarioCopy(
  ctx: CommandContext,
  input: {
    baseModelVersionId: ModelVersionId
    name: string
    ownerRoleId: RoleId
    scope: Json
  }
): Result<ScenarioCopy> {
  requirePermission(ctx, { action: "create", resourceType: "scenario_copy", branch: Branch.SCENARIO_RISK_CONTOUR_X01 })

  let copyVersion = cloneModelVersion(input.baseModelVersionId, {
    copyMode: "scenario",
    scope: input.scope
  })

  let scenario = {
    scenarioId: uuid(),
    tenantId: ctx.tenantId,
    baseModelVersionId: input.baseModelVersionId,
    scenarioModelVersionId: copyVersion.id,
    name: input.name,
    ownerRoleId: input.ownerRoleId,
    status: ScenarioStatus.SCENARIO_COPY
  }

  persistScenarioCopy(scenario)
  appendOnlyEvent(ctx, "scenario_copy_created", scenario.scenarioId, scenario)

  return ok(scenario, [])
}

function addScenarioAssumption(
  ctx: CommandContext,
  input: Omit<ScenarioAssumption, "assumptionId">
): Result<ScenarioAssumption> {
  requirePermission(ctx, { action: "update", resourceType: "scenario_copy" })

  let scenario = loadScenarioCopy(input.scenarioId)
  assertScenarioIsEditable(scenario)

  let assumption = {
    assumptionId: uuid(),
    scenarioId: input.scenarioId,
    targetEntityType: input.targetEntityType,
    targetEntityId: input.targetEntityId,
    assumptionType: input.assumptionType,
    value: input.value,
    source: input.source,
    trustLevel: input.trustLevel
  }

  applyAssumptionToScenarioModel(scenario.scenarioModelVersionId, assumption)
  persistScenarioAssumption(assumption)
  appendOnlyEvent(ctx, "scenario_assumption_added", assumption.assumptionId, assumption)

  return ok(assumption, [])
}

function runScenarioForecast(
  ctx: CommandContext,
  scenarioId: ScenarioId
): Result<ScenarioForecast> {
  requirePermission(ctx, { action: "create", resourceType: "scenario_forecast", branch: Branch.SCENARIO_RISK_CONTOUR_X01 })

  let scenario = loadScenarioCopy(scenarioId)
  let assumptions = loadScenarioAssumptions(scenarioId)
  let affectedBranches = detectAffectedBranches(assumptions)

  let outputs = {}
  let blockers = []
  let risks = []

  if (affectedBranches.includes(Branch.MAIN_VALUE_CREATION_B06)) {
    let demand = buildScenarioDemand(scenario, assumptions)
    let program = compileObjectModelToProductionProgram(ctx.withModelVersion(scenario.scenarioModelVersionId), demand).value
    outputs["Основное создание ценности (B06)"] = program
    blockers.addAll(program.bottlenecks.map(b => errorCode("SCENARIO_BOTTLENECK", b.effect, b.targetId)))
    risks.addAll(program.riskSignals)
  }

  if (affectedBranches.includes(Branch.FINANCE_AND_ECONOMICS_B10)) {
    outputs["Финансы и экономика (B10)"] = calculateScenarioFinancialEffects(scenario, outputs)
  }

  if (affectedBranches.includes(Branch.INVESTMENT_ANALYSIS_B11)) {
    outputs["Инвестиционный анализ (B11)"] = calculateScenarioInvestmentCase(scenario, outputs)
  }

  let forecast = {
    forecastId: uuid(),
    scenarioId,
    affectedBranches,
    calculatedAt: ctx.timestamp,
    outputs,
    blockers,
    risks,
    status: ScenarioStatus.FORECAST_CALCULATION
  }

  persistScenarioForecast(forecast)
  appendOnlyEvent(ctx, "scenario_forecast_calculated", forecast.forecastId, forecast)

  return ok(forecast, [])
}

function approveScenarioChange(
  ctx: CommandContext,
  input: {
    scenarioId: ScenarioId
    forecastId: ForecastId
    approvalScope: Json
    approvedImplementationPlan: Json
  }
): Result<ApprovedChange> {
  requireDecisionAuthority(ctx, "scenario_change_approval", input.approvalScope, TrustLevel.CONFIRMED)

  let forecast = loadScenarioForecast(input.forecastId)

  if (forecast.status != ScenarioStatus.FORECAST_CALCULATION) {
    throw error("FORECAST_STATUS_INVALID", "Утверждать можно только прогнозный расчет", input.forecastId)
  }

  if (forecast.blockers.hasBlocking()) {
    throw error("FORECAST_HAS_BLOCKERS", "Сценарий имеет блокирующие ограничения", input.forecastId)
  }

  let approved = {
    approvedChangeId: uuid(),
    scenarioId: input.scenarioId,
    forecastId: input.forecastId,
    approvedBy: ctx.userId,
    approvedAt: ctx.timestamp,
    implementationPlan: input.approvedImplementationPlan,
    status: ScenarioStatus.APPROVED_CHANGE
  }

  persistApprovedChange(approved)
  appendOnlyEvent(ctx, "scenario_change_approved", approved.approvedChangeId, approved)

  return ok(approved, [])
}

function commitImplementationFact(
  ctx: CommandContext,
  input: {
    approvedChangeId: UUID
    implementationFacts: FactEventInput[]
    evidence: Evidence[]
  }
): Result<ModelVersion> {
  requirePermission(ctx, { action: "update", resourceType: "working_model" })
  requireEvidenceForSignificantFact("scenario_implementation_fact", input.evidence)

  let approved = loadApprovedChange(input.approvedChangeId)
  let workingModel = loadWorkingModel(ctx.tenantId)

  for (let fact of input.implementationFacts) {
    recordFactEvent(ctx, fact)
    applyFactToWorkingModel(workingModel.id, fact)
  }

  let newVersion = createModelVersionFromWorkingModel(workingModel, {
    reason: "scenario_implemented",
    approvedChangeId: input.approvedChangeId
  })

  appendOnlyEvent(ctx, "scenario_implementation_committed", newVersion.id, {
    approvedChangeId: input.approvedChangeId,
    implementationFacts: input.implementationFacts
  })

  return ok(newVersion, [])
}
15. Доверенные показатели и раскрытие данных
tstype TrustedIndicatorInput = {
  indicatorCode: string
  methodVersionId: MethodVersionId
  sourceFactEventIds: EventId[]
  comparabilityClass: string
  disclosureMode: DisclosureMode
  applicabilityScope: Json
}

type TrustedIndicator = {
  trustedIndicatorId: UUID
  indicatorCode: string
  methodVersionId: MethodVersionId
  value: decimal
  normalizedValue?: decimal
  comparabilityClass: string
  completenessIndex: decimal
  trustIndex: decimal
  applicabilityScope: Json
  disclosureMode: DisclosureMode
  status: "draft" | "internal" | "disclosed" | "excluded"
}

function calculateTrustedIndicator(
  ctx: CommandContext,
  input: TrustedIndicatorInput
): Result<TrustedIndicator> {
  requirePermission(ctx, { action: "create", resourceType: "trusted_indicator" })

  let method = loadSignedMethod(input.methodVersionId)
  let facts = loadFactEvents(input.sourceFactEventIds)

  validateFactsForMethod(method, facts)
  validateComparabilityClass(input.comparabilityClass, facts, method)

  let completeness = calculateCompletenessIndex(facts, method.requiredFacts)
  let trustIndex = calculateTrustIndex(facts)

  if (completeness < method.minCompleteness || trustIndex < method.minTrustIndex) {
    return ok({
      trustedIndicatorId: uuid(),
      indicatorCode: input.indicatorCode,
      methodVersionId: input.methodVersionId,
      value: 0,
      comparabilityClass: input.comparabilityClass,
      completenessIndex: completeness,
      trustIndex,
      applicabilityScope: input.applicabilityScope,
      disclosureMode: input.disclosureMode,
      status: "excluded"
    }, [])
  }

  let value = method.calculate(facts)
  let normalizedValue = method.normalize ? method.normalize(value, input.comparabilityClass) : undefined

  let indicator = {
    trustedIndicatorId: uuid(),
    indicatorCode: input.indicatorCode,
    methodVersionId: input.methodVersionId,
    value,
    normalizedValue,
    comparabilityClass: input.comparabilityClass,
    completenessIndex: completeness,
    trustIndex,
    applicabilityScope: input.applicabilityScope,
    disclosureMode: input.disclosureMode,
    status: "internal"
  }

  if (input.disclosureMode != DisclosureMode.INTERNAL_ONLY) {
    validateIndicatorDisclosure(ctx, indicator)
    indicator.status = "disclosed"
  }

  persistTrustedIndicator(indicator)
  linkIndicatorFacts(indicator.trustedIndicatorId, facts)
  appendOnlyEvent(ctx, "trusted_indicator_calculated", indicator.trustedIndicatorId, indicator)

  return ok(indicator, [])
}

function validateIndicatorDisclosure(ctx: CommandContext, indicator: TrustedIndicator): void {
  requirePermission(ctx, {
    action: "disclose",
    resourceType: "trusted_indicator",
    disclosureMode: indicator.disclosureMode
  })

  let permission = loadDisclosurePermission(ctx.tenantId, indicator.disclosureMode, indicator.indicatorCode)

  if (permission == null || !permission.active) {
    throw error("DISCLOSURE_NOT_ALLOWED", "Нет разрешения на раскрытие показателя", indicator.indicatorCode)
  }

  if (indicator.completenessIndex < permission.minCompletenessIndex) {
    throw error("DISCLOSURE_COMPLETENESS_LOW", "Полнота ниже порога раскрытия", indicator.indicatorCode)
  }

  if (indicator.trustIndex < permission.minTrustIndex) {
    throw error("DISCLOSURE_TRUST_LOW", "Доверие ниже порога раскрытия", indicator.indicatorCode)
  }
}

function excludeFromTrustedProfile(
  ctx: CommandContext,
  trustedIndicatorId: UUID,
  reason: string
): Result<TrustedIndicator> {
  requireDecisionAuthority(ctx, "trusted_indicator_exclusion", { branch: Branch.IT_DATA_AND_DOCUMENTS_B16 }, TrustLevel.CONFIRMED)

  let indicator = loadTrustedIndicator(trustedIndicatorId)
  indicator.status = "excluded"

  persistTrustedIndicator(indicator)
  appendOnlyEvent(ctx, "trusted_indicator_excluded", trustedIndicatorId, { reason })

  return ok(indicator, [])
}
16. Факты, события, доказательства и ручные корректировки
tstype FactEventInput = {
  eventType: string
  occurredAt?: Timestamp
  entityType?: string
  entityId?: UUID
  payload: Json
  evidence: Evidence[]
}

type FactEvent = {
  eventId: EventId
  tenantId: TenantId
  modelVersionId: ModelVersionId
  eventType: string
  occurredAt: Timestamp
  recordedAt: Timestamp
  recordedBy?: UserId
  source: CommandContext.source
  entityType?: string
  entityId?: UUID
  payload: Json
  trustStatus: FactTrustStatus
  previousEventId?: EventId
}

function recordFactEvent(
  ctx: CommandContext,
  input: FactEventInput
): Result<FactEvent> {
  requirePermission(ctx, { action: "create", resourceType: "fact_event" })

  let trust = verifyFactTrust(ctx, input)

  let event = {
    eventId: uuid(),
    tenantId: ctx.tenantId,
    modelVersionId: ctx.modelVersionId,
    eventType: input.eventType,
    occurredAt: input.occurredAt ?? ctx.timestamp,
    recordedAt: ctx.timestamp,
    recordedBy: ctx.userId,
    source: ctx.source,
    entityType: input.entityType,
    entityId: input.entityId,
    payload: input.payload,
    trustStatus: trust.status,
    previousEventId: trust.previousEventId
  }

  appendOnlyEventRecord(event)

  for (let evidence of input.evidence) {
    attachEvidence(ctx, event.eventId, evidence)
  }

  if (trust.status in [
    FactTrustStatus.CONTRADICTION,
    FactTrustStatus.LIKELY_ERROR,
    FactTrustStatus.LIKELY_MANIPULATION
  ]) {
    createTrustReviewTask(ctx, event, trust)
  }

  return ok(event, [event.eventId])
}

function appendOnlyEvent(
  ctx: CommandContext,
  eventType: string,
  entityId: UUID,
  payload: Json
): FactEvent {
  return recordFactEvent(ctx, {
    eventType,
    entityType: inferEntityType(eventType),
    entityId,
    payload,
    evidence: []
  }).value
}

function attachEvidence(ctx: CommandContext, eventId: EventId, evidence: Evidence): void {
  let event = loadFactEvent(eventId)

  if (event.tenantId != ctx.tenantId) {
    throw error("TENANT_MISMATCH", "Доказательство не может быть привязано к чужому событию", eventId)
  }

  persistEvidence(eventId, evidence)
  updateEventTrustAfterEvidence(eventId)
}

function verifyFactTrust(
  ctx: CommandContext,
  input: FactEventInput
): {
  status: FactTrustStatus
  previousEventId?: EventId
  reason?: string
} {
  let previous = input.entityId ? loadLatestFactForEntity(input.entityId, input.eventType) : null

  if (input.occurredAt != null && input.occurredAt > ctx.timestamp) {
    return { status: FactTrustStatus.LIKELY_ERROR, previousEventId: previous?.eventId, reason: "future_occurrence" }
  }

  if (input.occurredAt != null && isBackdated(input.occurredAt, ctx.timestamp)) {
    return { status: FactTrustStatus.BACKDATED, previousEventId: previous?.eventId, reason: "backdated" }
  }

  if (previous != null && contradictsPreviousFact(previous.payload, input.payload)) {
    return { status: FactTrustStatus.CONTRADICTION, previousEventId: previous.eventId, reason: "contradicts_previous" }
  }

  if (input.evidence.isEmpty() && factTypeRequiresEvidence(input.eventType)) {
    return { status: FactTrustStatus.NEEDS_CONFIRMATION, previousEventId: previous?.eventId, reason: "evidence_missing" }
  }

  if (ctx.source == "ai_agent") {
    return { status: FactTrustStatus.NEEDS_CONFIRMATION, previousEventId: previous?.eventId, reason: "ai_not_source_of_truth" }
  }

  return { status: FactTrustStatus.DATA_ACCEPTED, previousEventId: previous?.eventId }
}

function processManualCorrection(
  ctx: CommandContext,
  input: {
    correctedEventId: EventId
    correctionPayload: Json
    reason: string
    evidence: Evidence[]
  }
): Result<FactEvent> {
  requireDecisionAuthority(ctx, "manual_fact_correction", { branch: Branch.IT_DATA_AND_DOCUMENTS_B16 }, TrustLevel.CONFIRMED)

  let original = loadFactEvent(input.correctedEventId)

  let correction = recordFactEvent(ctx, {
    eventType: "manual_correction",
    entityType: original.entityType,
    entityId: original.entityId,
    payload: {
      correctedEventId: input.correctedEventId,
      correctionPayload: input.correctionPayload,
      reason: input.reason
    },
    evidence: input.evidence
  }).value

  markEventAsSupersededByCorrection(original.eventId, correction.eventId)

  return ok(correction, [correction.eventId])
}
17. Интеграции, импорт и обезличенный статистический след
tstype ImportBatch = {
  batchId: UUID
  tenantId: TenantId
  sourceId: UUID
  sourceSystem: string
  importedAt: Timestamp
  rawRecordCount: int
  acceptedRecordCount: int
  rejectedRecordCount: int
  status: "received" | "validated" | "partially_accepted" | "rejected" | "posted"
}

type AnonymizedStatisticalTrace = {
  traceId: UUID
  tenantId?: null
  sourceScope: Json
  normalizedClass: string
  records: Json[]
  reproducibilityHash: string
  exportedAt: Timestamp
}

function importBatch(
  ctx: CommandContext,
  input: {
    sourceId: UUID
    sourceSystem: string
    records: Json[]
    mappingProfileId: UUID
  }
): Result<ImportBatch> {
  requirePermission(ctx, { action: "create", resourceType: "import_batch" })

  let mapping = loadMappingProfile(input.mappingProfileId)
  let accepted = []
  let rejected = []

  for (let raw of input.records) {
    let mapped = applyMapping(mapping, raw)
    let validation = validateMappedRecord(mapped)

    if (validation.ok) {
      accepted.push(mapped)
    } else {
      rejected.push({ raw, errors: validation.errors })
    }
  }

  let batch = {
    batchId: uuid(),
    tenantId: ctx.tenantId,
    sourceId: input.sourceId,
    sourceSystem: input.sourceSystem,
    importedAt: ctx.timestamp,
    rawRecordCount: input.records.count,
    acceptedRecordCount: accepted.count,
    rejectedRecordCount: rejected.count,
    status: rejected.isEmpty() ? "validated" : accepted.isEmpty() ? "rejected" : "partially_accepted"
  }

  persistImportBatch(batch)

  for (let record of accepted) {
    recordFactEvent(ctx, {
      eventType: record.eventType,
      occurredAt: record.occurredAt,
      entityType: record.entityType,
      entityId: record.entityId,
      payload: record.payload,
      evidence: [buildIntegrationEvidence(input.sourceId, batch.batchId, record)]
    })
  }

  appendOnlyEvent(ctx, "import_batch_processed", batch.batchId, { batch, rejected })

  return ok(batch, [])
}

function exportAnonymizedStatisticalTrace(
  ctx: CommandContext,
  input: {
    sourceScope: Json
    normalizedClass: string
    factEventIds: EventId[]
    disclosurePermissionId: UUID
  }
): Result<AnonymizedStatisticalTrace> {
  requirePermission(ctx, {
    action: "disclose",
    resourceType: "anonymized_statistical_trace",
    disclosureMode: DisclosureMode.PLATFORM_ANONYMIZED_TRACE
  })

  let permission = loadDisclosurePermissionById(input.disclosurePermissionId)
  validatePermissionCoversScope(permission, input.sourceScope)

  let facts = loadFactEvents(input.factEventIds)
  let normalized = normalizeFactsForStatisticalTrace(facts, input.normalizedClass)
  let anonymized = removeTenantPeopleObjectCommercialIdentifiers(normalized)

  validateAnonymization(anonymized)
  validateTraceReproducibility(anonymized)

  let trace = {
    traceId: uuid(),
    tenantId: null,
    sourceScope: stripSensitiveScope(input.sourceScope),
    normalizedClass: input.normalizedClass,
    records: anonymized,
    reproducibilityHash: hash(anonymized),
    exportedAt: ctx.timestamp
  }

  persistAnonymizedTrace(trace)
  appendOnlyEvent(ctx, "anonymized_statistical_trace_exported", trace.traceId, {
    sourceFactCount: facts.count,
    normalizedClass: input.normalizedClass,
    reproducibilityHash: trace.reproducibilityHash
  })

  return ok(trace, [])
}
18. Ролевой АРМ как проекция единого поля работы
tstype ArmContextRequest = {
  userId: UserId
  roleIds?: RoleId[]
  branch?: Branch
  objectId?: ObjectId
  processId?: UUID
  detailLevel?: "summary" | "work" | "control" | "reference" | "training"
}

type ArmContext = {
  userId: UserId
  activeRoles: RoleId[]
  responsibilityScope: Json
  visibleObjects: ValueChainObject[]
  visibleOperations: TechnologicalOperation[]
  visibleTasks: Task[]
  visibleDocuments: UUID[]
  visibleRisks: RiskSignal[]
  allowedActions: PermissionRequest[]
  controlPoints: Json[]
  referenceContext: Json
  trainingHints: Json[]
}

function generateArmContext(
  ctx: CommandContext,
  request: ArmContextRequest
): Result<ArmContext> {
  requirePermission(ctx, { action: "read", resourceType: "arm_context" })

  let roles = request.roleIds ?? loadActiveRoleAssignments(ctx.tenantId, request.userId, ctx.personId, ctx.timestamp).roleIds
  let scope = mergeRoleScopes(roles)

  let visibleData = filterVisibleDataForRole(ctx, {
    roles,
    scope,
    branch: request.branch,
    objectId: request.objectId,
    processId: request.processId,
    detailLevel: request.detailLevel ?? "work"
  })

  let armContext = {
    userId: request.userId,
    activeRoles: roles,
    responsibilityScope: scope,
    visibleObjects: visibleData.objects,
    visibleOperations: visibleData.operations,
    visibleTasks: visibleData.tasks,
    visibleDocuments: visibleData.documents,
    visibleRisks: visibleData.risks,
    allowedActions: determineAllowedActions(ctx, roles, visibleData),
    controlPoints: determineMandatoryControlPoints(roles, visibleData),
    referenceContext: buildReferenceContext(roles, visibleData),
    trainingHints: buildTrainingHints(roles, visibleData)
  }

  persistArmConfigurationSnapshot(ctx, armContext)

  return ok(armContext, [])
}

function filterVisibleDataForRole(
  ctx: CommandContext,
  input: {
    roles: RoleId[]
    scope: Json
    branch?: Branch
    objectId?: ObjectId
    processId?: UUID
    detailLevel: ArmContextRequest.detailLevel
  }
): {
  objects: ValueChainObject[]
  operations: TechnologicalOperation[]
  tasks: Task[]
  documents: UUID[]
  risks: RiskSignal[]
} {
  let permissions = loadPermissions(input.roles)

  return {
    objects: queryObjects(input.scope, input.objectId).filter(object => canRead(permissions, "value_chain_object", object)),
    operations: queryOperations(input.scope, input.processId).filter(operation => canRead(permissions, "technological_operation", operation)),
    tasks: queryOpenTasks(input.roles, input.scope),
    documents: queryDocuments(input.scope).filter(document => canRead(permissions, "document", document)),
    risks: queryRisks(input.scope).filter(risk => canRead(permissions, "risk_signal", risk))
  }
}

function determineAllowedActions(
  ctx: CommandContext,
  roles: RoleId[],
  visibleData: Json
): PermissionRequest[] {
  let permissions = loadPermissions(roles)
  let actions = []

  for (let permission of permissions) {
    if (permissionCanBeRenderedAsAction(permission, visibleData)) {
      actions.push(permissionToAction(permission))
    }
  }

  return actions
}

function buildTrainingHints(roles: RoleId[], visibleData: Json): Json[] {
  let hints = []

  for (let task of visibleData.tasks) {
    hints.push(loadTrainingHintForTask(task.taskType, roles))
  }

  for (let risk of visibleData.risks) {
    hints.push(loadTrainingHintForRisk(risk.riskType, roles))
  }

  return compact(hints)
}
19. Офлайн-режим, локальный журнал и синхронизация
tstype OfflineEvent = {
  offlineEventId: UUID
  tenantId: TenantId
  deviceId: UUID
  localSequence: int
  createdAtLocal: Timestamp
  commandType: string
  payload: Json
  modelVersionIdAtCreation: ModelVersionId
  status: "pending" | "synced" | "conflict" | "rejected"
}

type SyncResult = {
  acceptedEvents: EventId[]
  conflicts: Conflict[]
  rejectedEvents: OfflineEvent[]
}

type Conflict = {
  conflictId: UUID
  offlineEventId: UUID
  conflictType: "version_mismatch" | "contradictory_fact" | "permission_changed" | "stale_decision" | "duplicate"
  serverState: Json
  clientState: Json
  requiredOwnerRoleIds: RoleId[]
}

function recordOfflineEvent(
  ctx: CommandContext,
  commandType: string,
  payload: Json
): Result<OfflineEvent> {
  if (!isCommandAllowedOffline(commandType)) {
    throw error("OFFLINE_COMMAND_FORBIDDEN", "Команда требует актуального внешнего решения", commandType)
  }

  let offlineEvent = {
    offlineEventId: uuid(),
    tenantId: ctx.tenantId,
    deviceId: ctx.deviceId,
    localSequence: nextLocalSequence(ctx.deviceId),
    createdAtLocal: ctx.timestamp,
    commandType,
    payload,
    modelVersionIdAtCreation: ctx.modelVersionId,
    status: "pending"
  }

  persistLocalOfflineEvent(offlineEvent)

  return ok(offlineEvent, [])
}

function syncOfflineJournal(
  ctx: CommandContext,
  deviceId: UUID
): Result<SyncResult> {
  requirePermission(ctx, { action: "create", resourceType: "offline_sync" })

  let pending = loadPendingOfflineEvents(deviceId).sortBy(event => event.localSequence)
  let accepted = []
  let conflicts = []
  let rejected = []

  for (let offline of pending) {
    let versionCheck = checkModelVersionCompatibility(offline.modelVersionIdAtCreation, currentModelVersion(ctx.tenantId))

    if (!versionCheck.ok) {
      let conflict = resolveVersionConflict(ctx, offline, versionCheck)
      conflicts.push(conflict)
      markOfflineEventConflict(offline.offlineEventId, conflict.conflictId)
      continue
    }

    if (!stillHasPermission(ctx, offline.commandType, offline.payload)) {
      rejected.push(offline)
      markOfflineEventRejected(offline.offlineEventId, "permission_changed")
      continue
    }

    let result = replayOfflineCommand(ctx, offline)

    if (result.ok) {
      accepted.addAll(result.events)
      markOfflineEventSynced(offline.offlineEventId, result.events)
    } else if (result.errors.any(error => error.code == "CONTRADICTION")) {
      let conflict = createConflictFromReplayError(offline, result.errors)
      conflicts.push(conflict)
      markOfflineEventConflict(offline.offlineEventId, conflict.conflictId)
    } else {
      rejected.push(offline)
      markOfflineEventRejected(offline.offlineEventId, result.errors.first().code)
    }
  }

  return ok({ acceptedEvents: accepted, conflicts, rejectedEvents: rejected }, accepted)
}

function resolveVersionConflict(ctx: CommandContext, offline: OfflineEvent, versionCheck: Json): Conflict {
  let conflict = {
    conflictId: uuid(),
    offlineEventId: offline.offlineEventId,
    conflictType: "version_mismatch",
    serverState: versionCheck.serverState,
    clientState: offline.payload,
    requiredOwnerRoleIds: findOwnersForConflict(offline, versionCheck)
  }

  persistConflict(conflict)
  createArmTask(ctx, {
    branch: detectBranchFromCommand(offline.commandType),
    roleIds: conflict.requiredOwnerRoleIds,
    taskType: "resolve_offline_conflict",
    targetEntityType: "conflict",
    targetEntityId: conflict.conflictId
  })

  return conflict
}
20. AI-оркестрация и контроль вывода
tstype AiRequest = {
  aiRequestId: UUID
  tenantId: TenantId
  requestedBy: UserId
  requestType: "draft_model" | "analyze_gap" | "find_risk" | "explain_deviation" | "prepare_decision_package"
  inputScope: Json
  allowedDataRefs: UUID[]
  forbiddenDataClasses: string[]
  status: "created" | "running" | "needs_review" | "accepted" | "rejected"
}

type AiAnalysisPackage = {
  packageId: UUID
  aiRequestId: UUID
  claims: AiClaim[]
  calculations: Json[]
  sourceRefs: UUID[]
  limitations: string[]
  validationStatus: "not_checked" | "passed" | "failed" | "needs_human_review"
}

type AiClaim = {
  claimId: UUID
  text: string
  claimType: "fact" | "hypothesis" | "recommendation" | "calculation_result" | "risk"
  sourceRefs: UUID[]
  confidence: decimal
  mayCreateFact: boolean
}

function requestAiAnalysis(
  ctx: CommandContext,
  input: {
    requestType: AiRequest.requestType
    inputScope: Json
    allowedDataRefs: UUID[]
    forbiddenDataClasses: string[]
  }
): Result<AiRequest> {
  requirePermission(ctx, { action: "create", resourceType: "ai_request", branch: Branch.AI_AND_AGENT_CONTOUR_X02 })

  validateDigitalMirrorReadiness(ctx, input.inputScope)

  let request = {
    aiRequestId: uuid(),
    tenantId: ctx.tenantId,
    requestedBy: ctx.userId,
    requestType: input.requestType,
    inputScope: input.inputScope,
    allowedDataRefs: input.allowedDataRefs,
    forbiddenDataClasses: input.forbiddenDataClasses,
    status: "created"
  }

  persistAiRequest(request)
  appendOnlyEvent(ctx, "ai_request_created", request.aiRequestId, request)

  return ok(request, [])
}

function validateDigitalMirrorReadiness(ctx: CommandContext, inputScope: Json): void {
  let required = loadAiReadinessRequirements(inputScope)
  let missing = []

  for (let requirement of required) {
    if (!digitalMirrorHasRequirement(ctx.tenantId, ctx.modelVersionId, requirement)) {
      missing.push(requirement)
    }
  }

  if (!missing.isEmpty()) {
    throw error("DIGITAL_MIRROR_NOT_READY", "Недостаточно модели для AI-анализа", missing.join(", "))
  }
}

function runAiOrchestration(
  ctx: CommandContext,
  aiRequestId: UUID
): Result<AiAnalysisPackage> {
  let request = loadAiRequest(aiRequestId)
  let inputData = loadAllowedAiInputData(request.allowedDataRefs, request.forbiddenDataClasses)
  let rawOutput = runConfiguredAiAgents(request.requestType, inputData)

  let package = {
    packageId: uuid(),
    aiRequestId,
    claims: parseAiClaims(rawOutput),
    calculations: parseAiCalculations(rawOutput),
    sourceRefs: extractSourceRefs(rawOutput),
    limitations: extractLimitations(rawOutput),
    validationStatus: "not_checked"
  }

  package.validationStatus = validateAiOutput(ctx, package)

  persistAiAnalysisPackage(package)
  appendOnlyEvent(ctx, "ai_analysis_package_created", package.packageId, package)

  return ok(package, [])
}

function validateAiOutput(ctx: CommandContext, package: AiAnalysisPackage): AiAnalysisPackage.validationStatus {
  for (let claim of package.claims) {
    if (claim.mayCreateFact) {
      return "failed"
    }

    if (claim.sourceRefs.isEmpty()) {
      return "needs_human_review"
    }

    if (!sourcesAllowedForRequest(package.aiRequestId, claim.sourceRefs)) {
      return "failed"
    }
  }

  if (!calculationsReproducible(package.calculations)) {
    return "needs_human_review"
  }

  let criticFindings = runArchitectureCritic(ctx, {
    targetType: "ai_analysis_package",
    targetId: package.packageId,
    content: package
  })

  if (criticFindings.any(finding => finding.severity == "blocking")) {
    return "failed"
  }

  return "passed"
}
21. Шлюз содержательного предложения и критик архитектуры
tstype GatewayInput = {
  moduleCode: string
  proposalText: string
  role: string
  crossFlow: string
  depthClass: ModelDepth
  instrumentCombination: string[]
  interfaces: string[]
  userSurface: string
  causalityChain: string[]
}

type GatewayResult = {
  passed: boolean
  missing: string[]
  normalizedProposal?: Json
}

function runModuleGateway(input: GatewayInput): GatewayResult {
  let missing = []

  if (isBlank(input.role)) missing.push("role")
  if (isBlank(input.crossFlow)) missing.push("crossFlow")
  if (input.depthClass == null) missing.push("depthClass")
  if (input.instrumentCombination.isEmpty()) missing.push("instrumentCombination")
  if (input.interfaces.isEmpty()) missing.push("interfaces")
  if (isBlank(input.userSurface)) missing.push("userSurface")
  if (input.causalityChain.count < 2) missing.push("causalityChain")

  if (!causalityHasInputProcessOutput(input.causalityChain)) {
    missing.push("input_process_output_causality")
  }

  return {
    passed: missing.isEmpty(),
    missing,
    normalizedProposal: missing.isEmpty() ? normalizeProposal(input) : undefined
  }
}

type CriticTarget = {
  targetType: "module" | "function" | "api_contract" | "data_schema" | "ai_analysis_package"
  targetId: UUID | string
  content: Json | string
}

type CriticFinding = {
  findingId: UUID
  severity: "info" | "low" | "medium" | "high" | "blocking"
  defectClass:
    | "calculation_gap"
    | "causal_gap"
    | "status_gap"
    | "owner_gap"
    | "integration_gap"
    | "data_contract_gap"
    | "evidence_gap"
    | "security_gap"
    | "applicability_gap"
    | "applied_risk"
  exactPlace: string
  failingChain: string[]
  expectedChain: string[]
  proposedCorrection: string
  proof: string
}

function runArchitectureCritic(ctx: CommandContext, target: CriticTarget): CriticFinding[] {
  let findings = []

  findings.addAll(findCalculationGaps(target))
  findings.addAll(findCausalGaps(target))
  findings.addAll(findStatusGaps(target))
  findings.addAll(findOwnerGaps(target))
  findings.addAll(findIntegrationGaps(target))
  findings.addAll(findDataContractGaps(target))
  findings.addAll(findEvidenceGaps(target))
  findings.addAll(findSecurityGaps(target))
  findings.addAll(findApplicabilityGaps(target))
  findings.addAll(findAppliedRisks(target))

  findings = findings.filter(finding => isSubstantiveFinding(finding))
  findings = findings.filter(finding => !isTerminologyOnlyFinding(finding))
  findings = findings.filter(finding => !justRepeatsAcceptedDecision(finding, target))
  findings = mergeDuplicates(findings)
  findings = sortBySeverityAndCausalImpact(findings)

  appendOnlyEvent(ctx, "architecture_critic_finished", target.targetId as UUID, {
    targetType: target.targetType,
    findingCount: findings.count,
    findings
  })

  return findings
}

function isSubstantiveFinding(finding: CriticFinding): boolean {
  if (finding.failingChain.isEmpty() || finding.expectedChain.isEmpty()) {
    return false
  }

  if (finding.proposedCorrection == "") {
    return false
  }

  if (finding.defectClass == null) {
    return false
  }

  return true
}

function isTerminologyOnlyFinding(finding: CriticFinding): boolean {
  let text = (finding.proof + " " + finding.proposedCorrection).toLowerCase()

  if (text.contains("переименовать") && !text.contains("ломает вход") && !text.contains("ломает выход")) {
    return true
  }

  if (text.contains("разделить понятия") && !hasCalculationOrCausalityImpact(finding)) {
    return true
  }

  return false
}

function justRepeatsAcceptedDecision(finding: CriticFinding, target: CriticTarget): boolean {
  let acceptedDecisions = loadAcceptedArchitectureDecisions(target.targetId)

  return acceptedDecisions.any(decision =>
    semanticSame(decision.statement, finding.proposedCorrection) &&
    !finding.proof.contains("new_breakage_after_decision")
  )
}
22. Селективный межслойный обмен
tstype ExchangeMessage = {
  exchangeId: UUID
  sourceBranch: Branch
  targetBranch: Branch
  payloadType: string
  payload: Json
  detailLevel: string
  ownerRoleId?: RoleId
  trustLevel: TrustLevel
  status: "draft" | "sent" | "accepted" | "rejected" | "needs_clarification"
}

function selectiveExchange(
  ctx: CommandContext,
  input: {
    sourceBranch: Branch
    targetBranch: Branch
    payloadType: string
    payload: Json
    detailLevel: string
    ownerRoleId?: RoleId
  }
): Result<ExchangeMessage> {
  requirePermission(ctx, { action: "create", resourceType: "branch_exchange", branch: input.sourceBranch })

  let policy = loadExchangePolicy(input.sourceBranch, input.targetBranch, input.payloadType)
  let reducedPayload = reducePayloadToAllowedDetail(input.payload, policy.allowedFields, input.detailLevel)
  let trustLevel = calculatePayloadTrustLevel(reducedPayload)

  if (policy.requiresOwner && input.ownerRoleId == null) {
    throw error("EXCHANGE_OWNER_REQUIRED", "Для стыка нужен владелец подтверждения", input.payloadType)
  }

  if (trustLevel < policy.minTrustLevel) {
    throw error("EXCHANGE_TRUST_LOW", "Доверие ниже порога передачи", input.payloadType)
  }

  let message = {
    exchangeId: uuid(),
    sourceBranch: input.sourceBranch,
    targetBranch: input.targetBranch,
    payloadType: input.payloadType,
    payload: reducedPayload,
    detailLevel: input.detailLevel,
    ownerRoleId: input.ownerRoleId,
    trustLevel,
    status: "sent"
  }

  persistExchangeMessage(message)
  appendOnlyEvent(ctx, "branch_exchange_sent", message.exchangeId, message)

  return ok(message, [])
}

function reducePayloadToAllowedDetail(payload: Json, allowedFields: string[], detailLevel: string): Json {
  let reduced = {}

  for (let field of allowedFields) {
    if (payload.has(field)) {
      reduced[field] = payload[field]
    }
  }

  if (detailLevel == "planning") {
    return removeDeepTechnicalDetails(reduced)
  }

  if (detailLevel == "cost_and_cash_effect") {
    return keepOnlyCostCashAndDriverFields(reduced)
  }

  if (detailLevel == "nomenclature_quantity_deadline") {
    return keepOnlyNomenclatureQuantityDeadline(reduced)
  }

  return reduced
}

function receiveExchangeMessage(
  ctx: CommandContext,
  exchangeId: UUID
): Result<ExchangeMessage> {
  let message = loadExchangeMessage(exchangeId)

  requirePermission(ctx, { action: "read", resourceType: "branch_exchange", branch: message.targetBranch })

  let validation = validateIncomingExchange(message)

  if (!validation.ok) {
    message.status = "needs_clarification"
    persistExchangeMessage(message)
    createClarificationTask(ctx, message, validation.errors)
    return ok(message, [])
  }

  message.status = "accepted"
  persistExchangeMessage(message)
  applyExchangeToTargetBranch(ctx, message)
  appendOnlyEvent(ctx, "branch_exchange_accepted", message.exchangeId, message)

  return ok(message, [])
}
23. Сквозные end-to-end сценарии
tsfunction endToEnd_CreateAndUseValueChainObject(ctx: CommandContext, input: Json): Result<Json> {
  let templateDecision = shouldCreateIndividualLegoElement(input.elementRequest, findNearestLegoTemplates(input.elementRequest.candidateAttributes))

  let template =
    templateDecision.createIndividual
      ? createIndividualLegoTemplate(ctx, input.elementRequest).value
      : loadTemplate(templateDecision.useNearestTemplate)

  let instance = createLocalInstanceFromTemplate(ctx, template.templateId, input.localAttributes).value

  let object = createValueChainObject(ctx, {
    localInstanceId: instance.localInstanceId,
    objectKind: input.objectKind,
    name: input.name,
    roleInValueChain: input.roleInValueChain,
    inputParameters: input.inputParameters,
    outputParameters: input.outputParameters,
    operatingEnvelope: input.operatingEnvelope,
    criticalityClass: input.criticalityClass
  }).value

  for (let nodeInput of input.nodes) {
    createObjectNode(ctx, { ...nodeInput, objectId: object.objectId })
  }

  for (let contourInput of input.interfaceContours) {
    linkInterfaceContour(ctx, contourInput)
  }

  return ok({ template, instance, object }, [])
}

function endToEnd_OperateDetectRepairRecalculate(ctx: CommandContext, input: Json): Result<Json> {
  let execution = executeOperation(ctx, input.operationExecution).value

  if (!execution.feasible) {
    return ok({ status: "not_feasible", execution }, [])
  }

  let balance = closeOperationWithFact(ctx, input.operationFact).value
  let signals = input.measurements.map(measurement => detectWeakSignal(ctx, measurement).value)

  let defects = []
  for (let signal of signals.filter(signal => signal.isWeakSignal)) {
    if (signal.class in [SignalClass.DEGRADATION_START, SignalClass.HIDDEN_DEFECT]) {
      defects.push(createDefectFromManifestation(ctx, {
        objectId: signal.objectId,
        nodeId: signal.nodeId,
        manifestationEventId: signal.signalId,
        symptoms: signal,
        evidence: []
      }).value)
    }
  }

  let repairActions = []
  for (let defect of defects) {
    let decision = chooseRepairStrategy(buildRepairDecisionInput(defect))
    repairActions.push(createRepairAction(ctx, {
      objectId: defect.objectId,
      nodeId: defect.nodeId,
      defectId: defect.defectId,
      decision,
      plannedWindow: chooseRepairWindow(decision),
      requiredParts: deriveRequiredParts(defect, decision),
      requiredRoles: deriveRequiredRoles(defect, decision)
    }).value)
  }

  return ok({ execution, balance, signals, defects, repairActions }, [])
}

function endToEnd_RunScenarioAndApprove(ctx: CommandContext, input: Json): Result<Json> {
  let scenario = createScenarioCopy(ctx, input.scenario).value

  for (let assumption of input.assumptions) {
    addScenarioAssumption(ctx, { ...assumption, scenarioId: scenario.scenarioId })
  }

  let forecast = runScenarioForecast(ctx, scenario.scenarioId).value

  if (forecast.blockers.hasBlocking()) {
    return ok({ scenario, forecast, status: "blocked_by_forecast" }, [])
  }

  let approved = approveScenarioChange(ctx, {
    scenarioId: scenario.scenarioId,
    forecastId: forecast.forecastId,
    approvalScope: input.approvalScope,
    approvedImplementationPlan: input.implementationPlan
  }).value

  return ok({ scenario, forecast, approved }, [])