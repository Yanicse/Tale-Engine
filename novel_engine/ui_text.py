"""Texts of the reader page, by language.

English is the reference: every key exists there. A language pack only translates; a missing key falls
back to English. A novel pack can still override any key through `labels` in its theme.json.
`{name}`-style placeholders are filled by the page and must be kept by translations.
"""
import re

EN = {
    'tab.story': 'Story', 'tab.scene': 'Scene', 'tab.npc': 'NPC', 'tab.possessions': 'Possessions',
    'tab.agenda': 'Agenda', 'tab.summaries': 'Summaries', 'tab.abilities': 'Abilities', 'tab.creations': 'Creations',
    'send': 'Send', 'dictate': 'Dictate', 'stop': 'Stop',
    'time.deep_night': 'Deep night', 'time.dawn': 'Dawn', 'time.morning': 'Morning', 'time.noon': 'Noon',
    'time.afternoon': 'Afternoon', 'time.dusk': 'Dusk', 'time.evening': 'Evening', 'time.night': 'Night',
    'mode.auto': 'Auto', 'mode.day': 'Day', 'mode.night': 'Night', 'mode.arcane': 'Arcane', 'mode.frost': 'Frost',
    'mode.scifi': 'Sci-fi',
    'theme.change': 'Change theme', 'theme.follows_time': 'Theme follows the fictional time (click to change the mood)',
    'theme.forced': 'Mood forced (click to change)',
    'audio.button': 'Sound', 'audio.label': 'Ambient sound', 'audio.title': 'Ambient sound (off by default)',
    'audio.unsupported': 'Audio not supported by this browser.',
    'settings.label': 'Reader settings', 'settings.text_size': 'Text size', 'settings.decrease': 'Decrease text size',
    'settings.increase': 'Increase text size', 'settings.immersive': 'Immersive mode', 'settings.text_only': 'Text only',
    'jump.latest': 'Go to the latest exchange',
    'input.hint': 'What {name} does... (Ctrl+Enter to send)', 'input.aria': 'Action', 'engine.aria': 'Narrator engine',
    'engine.manual': 'Manual (copy and paste)', 'engine.unavailable': ' (unavailable)', 'dictate.aria': 'Dictate the action',
    'title.default': 'Chronicle of {name}',
    'search.story': 'Search the story', 'search.button': 'Search', 'search.results': '{n} result(s) for “{q}”',
    'older': 'Older exchanges', 'open_story': 'Open in the story',
    'scene.none': 'No scene.', 'pending.title': 'Pending action', 'pending.badge': 'Pending',
    'pending.text': 'An action is waiting to be resolved: use Resume, Retry or Re-evaluate at the bottom of the page.',
    'purse.title': 'Purse', 'purse.none': 'No purse.', 'projects.title': 'Projects',
    'project.done': 'Done: ', 'project.todo': 'To do: ', 'project.check': 'To check: ',
    'clock.label': 'Clock: ', 'place.label': 'Place: ', 'deadlines.none': 'No known deadline recorded.',
    'npc.title': 'Non-player characters', 'npc.intro': 'Recorded sheets and story excerpts known to the character.',
    'npc.search': 'Search a name or a description', 'npc.search_aria': 'Search an NPC', 'npc.count': '{n} NPC of {total}',
    'status.label': 'Status: ', 'npc.passages': 'Story passages', 'npc.see': 'See in the story',
    'npc.no_description': 'No description recorded.', 'npc.no_match': 'No NPC matches the search.',
    'npc.none': 'No known NPC recorded.', 'summaries.none': 'No summary recorded.',
    'hp.title': 'Hit points', 'mana.title': 'Mana',
    'cooldown.available': 'Available', 'cooldown.remaining': 'Available in {n} fictional seconds', 'power.default': 'Default power: {n} %.', 'cost.limits': 'Cost: see the limits.',
    'cost.zero': 'Cost: 0 mana.', 'cost.default': 'Cost: {default} mana by default; {full} mana at 100%.',
    'limits.label': 'Limits: ', 'charges.left': 'Charges left: {n} ({period})', 'unknown': 'unknown',
    'abilities.always': 'Always active', 'abilities.day': 'Daytime abilities', 'abilities.night': 'Nighttime abilities',
    'abilities.other': 'Other abilities', 'filter.all': 'All', 'filter.day': 'Day', 'filter.night': 'Night',
    'creations.none': 'No accessible creation.', 'creations.inactive': 'Creations registry inactive.',
    'read.failed': 'Reading failed: ',
    'dictation.listening': 'Listening... review the text before sending.',
    'dictation.denied': 'Microphone denied: allow it in the browser.', 'dictation.no_voice': 'No voice detected.',
    'dictation.no_mic': 'No microphone found.', 'dictation.network': 'Speech recognition unavailable (network).',
    'dictation.interrupted': 'Dictation interrupted ({error}).', 'dictation.finished': 'Dictation finished: review, then send.',
    'dictation.failed': 'Dictation failed: ', 'dictation.unsupported': 'Dictation not supported by this browser (use Chrome or Edge).',
    'dictation.stopped': 'Dictation stopped: review the text, then send.',
    'turn.resume': 'Resume', 'turn.retry': 'Retry', 'turn.reevaluate': 'Re-evaluate',
    'turn.writing': 'The narrator is writing the next part...', 'turn.failure': 'Failure: ',
    'turn.recorded': 'Turn recorded.', 'turn.not_recorded': 'Turn not recorded ({status})', 'turn.sending': 'Sending...',
    'turn.refused': 'Refused: ',
}

FR = {
    'tab.story': 'Histoire', 'tab.scene': 'Scène', 'tab.npc': 'PNJ', 'tab.possessions': 'Possessions',
    'tab.agenda': 'Agenda', 'tab.summaries': 'Résumés', 'tab.abilities': 'Capacités', 'tab.creations': 'Créations',
    'send': 'Envoyer', 'dictate': 'Dicter', 'stop': 'Arrêter',
    'time.deep_night': 'Nuit profonde', 'time.dawn': 'Aube', 'time.morning': 'Matin', 'time.noon': 'Midi',
    'time.afternoon': 'Après-midi', 'time.dusk': 'Crépuscule', 'time.evening': 'Soir', 'time.night': 'Nuit',
    'mode.auto': 'Auto', 'mode.day': 'Jour', 'mode.night': 'Nuit', 'mode.arcane': 'Arcane', 'mode.frost': 'Givre',
    'mode.scifi': 'Science-fiction',
    'theme.change': 'Changer de thème', 'theme.follows_time': "Le thème suit l'heure fictive (cliquer pour changer d'ambiance)",
    'theme.forced': "Ambiance imposée (cliquer pour changer)",
    'audio.button': 'Son', 'audio.label': "Son d'ambiance", 'audio.title': "Son d'ambiance (désactivé par défaut)",
    'audio.unsupported': "L'audio n'est pas pris en charge par ce navigateur.",
    'settings.label': 'Réglages de lecture', 'settings.text_size': 'Taille du texte', 'settings.decrease': 'Réduire le texte',
    'settings.increase': 'Agrandir le texte', 'settings.immersive': 'Mode immersif', 'settings.text_only': 'Texte seul',
    'jump.latest': 'Aller au dernier échange',
    'input.hint': 'Que fait {name} ? (Ctrl+Entrée pour envoyer)', 'input.aria': 'Action', 'engine.aria': 'Moteur du narrateur',
    'engine.manual': 'Manuel (copier-coller)', 'engine.unavailable': ' (indisponible)', 'dictate.aria': "Dicter l'action",
    'title.default': 'Chronique de {name}',
    'search.story': "Chercher dans l'histoire", 'search.button': 'Chercher', 'search.results': '{n} résultat(s) pour « {q} »',
    'older': 'Échanges plus anciens', 'open_story': "Ouvrir dans l'histoire",
    'scene.none': 'Aucune scène.', 'pending.title': 'Action en attente', 'pending.badge': 'En attente',
    'pending.text': "Une action attend d'être résolue : utilisez Reprendre, Réessayer ou Réévaluer en bas de la page.",
    'purse.title': 'Bourse', 'purse.none': 'Aucune bourse.', 'projects.title': 'Projets',
    'project.done': 'Fait : ', 'project.todo': 'À faire : ', 'project.check': 'À vérifier : ',
    'clock.label': 'Horloge : ', 'place.label': 'Lieu : ', 'deadlines.none': 'Échéance connue : aucune.',
    'npc.title': 'Personnages non-joueurs', 'npc.intro': "Fiches enregistrées et extraits de l'histoire connus du personnage.",
    'npc.search': 'Chercher un nom ou une description', 'npc.search_aria': 'Chercher un PNJ', 'npc.count': '{n} PNJ sur {total}',
    'status.label': 'Statut : ', 'npc.passages': "Passages de l'histoire", 'npc.see': "Voir dans l'histoire",
    'npc.no_description': 'Aucune description enregistrée.', 'npc.no_match': 'Aucun PNJ ne correspond à la recherche.',
    'npc.none': 'Aucun PNJ connu.', 'summaries.none': 'Aucun résumé enregistré.',
    'hp.title': 'Points de vie', 'power.default': 'Puissance par défaut : {n} %.', 'cost.limits': 'Coût : voir les limites.',
    'cost.zero': EN['cost.zero'], 'cost.default': EN['cost.default'],
    'limits.label': 'Limites : ', 'charges.left': 'Charges restantes : {n} ({period})', 'unknown': 'inconnu',
    'abilities.always': 'Toujours actives', 'abilities.day': 'Capacités de jour', 'abilities.night': 'Capacités de nuit',
    'abilities.other': 'Autres capacités', 'filter.all': 'Toutes', 'filter.day': 'Jour', 'filter.night': 'Nuit',
    'creations.none': 'Aucune création accessible.', 'creations.inactive': 'Registre des créations inactif.',
    'read.failed': 'Échec de la lecture : ',
    'dictation.listening': "Écoute en cours... relisez le texte avant d'envoyer.",
    'dictation.denied': 'Micro refusé : autorisez-le dans le navigateur.', 'dictation.no_voice': 'Aucune voix détectée.',
    'dictation.no_mic': 'Aucun micro trouvé.', 'dictation.network': 'Reconnaissance vocale indisponible (réseau).',
    'dictation.interrupted': 'Dictée interrompue ({error}).', 'dictation.finished': 'Dictée terminée : relisez, puis envoyez.',
    'dictation.failed': 'La dictée a échoué : ', 'dictation.unsupported': "La dictée n'est pas prise en charge par ce navigateur (utilisez Chrome ou Edge).",
    'dictation.stopped': 'Dictée arrêtée : relisez le texte, puis envoyez.',
    'turn.resume': 'Reprendre', 'turn.retry': 'Réessayer', 'turn.reevaluate': 'Réévaluer',
    'turn.writing': 'Le narrateur écrit la suite...', 'turn.failure': 'Échec : ',
    'turn.recorded': 'Tour enregistré.', 'turn.not_recorded': 'Tour non enregistré ({status})', 'turn.sending': 'Envoi...',
    'turn.refused': 'Refusé : ',
}

PACKS = {'en': EN, 'fr': {**EN, **FR}}
KEYS = tuple(EN)
LANGUAGE_NAMES = {'english': 'en', 'anglais': 'en', 'french': 'fr', 'francais': 'fr', 'français': 'fr'}
PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')


def language_code(language):
    """'fr', 'fr-FR', 'French' or 'Français' -> 'fr'; anything unknown -> 'en'."""
    value = str(language or '').strip().casefold()
    value = LANGUAGE_NAMES.get(value, value.split('-')[0].split('_')[0])
    return value if value in PACKS else 'en'


def labels_for(language, overrides=None):
    """All reader texts for a language: English, then the language pack, then the novel pack's own labels."""
    labels = {**EN, **PACKS[language_code(language)]}
    labels.update({k: v for k, v in (overrides or {}).items() if k in EN and isinstance(v, str) and v})
    return labels
