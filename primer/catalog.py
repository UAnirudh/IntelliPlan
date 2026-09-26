"""Small reviewed catalog. Answers stay on the server."""

from dataclasses import dataclass


WORLDS = {
    'forest': {'name': 'The Forest', 'place': 'forest trail'},
    'space': {'name': 'The Stars', 'place': 'space station'},
    'ocean': {'name': 'The Ocean', 'place': 'ocean garden'},
}


@dataclass(frozen=True)
class Skill:
    id: str
    domain: str
    title: str
    prerequisite: str | None = None


SKILLS = (
    Skill('read_sounds', 'Reading', 'Beginning sounds'),
    Skill('read_sentences', 'Reading', 'Sentence meaning', 'read_sounds'),
    Skill('read_passages', 'Reading', 'Short passages', 'read_sentences'),
    Skill('write_order', 'Writing', 'Build a sentence'),
    Skill('write_punctuation', 'Writing', 'Capitals and punctuation', 'write_order'),
    Skill('write_clarity', 'Writing', 'Clear sentences', 'write_punctuation'),
    Skill('math_count', 'Arithmetic', 'Count objects'),
    Skill('math_add', 'Arithmetic', 'Add within ten', 'math_count'),
    Skill('math_story', 'Arithmetic', 'Story problems', 'math_add'),
)
SKILL_BY_ID = {skill.id: skill for skill in SKILLS}


@dataclass(frozen=True)
class Item:
    id: str
    skill_id: str
    prompt: str
    answer: str
    options: tuple[str, ...] = ()
    hint: str = ''
    explanation: str = ''


ITEMS = (
    Item('sound_m', 'read_sounds', 'At the {place}, which word begins with the /m/ sound?', 'moon', ('moon', 'sun', 'leaf'), 'Say each word slowly and listen to its first sound.', 'Moon begins with /m/.'),
    Item('sound_s', 'read_sounds', 'Which word begins with the /s/ sound?', 'sun', ('moon', 'sun', 'fish'), 'Stretch the first sound in each word.', 'Sun begins with /s/.'),
    Item('sentence_cat', 'read_sentences', 'Read: “The cat sits on the mat.” Where is the cat?', 'on the mat', ('on the mat', 'under the bed', 'in the tree'), 'Read the words after “sits.”', 'The sentence says the cat sits on the mat.'),
    Item('sentence_kite', 'read_sentences', 'Read: “Mia carries a red kite.” What does Mia carry?', 'a red kite', ('a red kite', 'a blue bag', 'a small book'), 'Look for the word after “carries.”', 'Mia carries a red kite.'),
    Item('passage_seed', 'read_passages', 'Read: “Lena plants a seed. She gives it water each day. A green leaf appears.” What helped the seed grow?', 'water', ('water', 'a shoe', 'a kite'), 'Read the second sentence again.', 'Lena watered the seed each day.'),
    Item('passage_map', 'read_passages', 'Read: “Noah finds a map. It shows a path to a bridge. He follows the path and sees the bridge.” What did the map show?', 'a path to a bridge', ('a path to a bridge', 'a boat', 'a mountain'), 'Look at the sentence that begins “It shows.”', 'The map showed a path to a bridge.'),
    Item('order_bird', 'write_order', 'Put these words in order: “sings / bird / The”. Type the sentence.', 'The bird sings.', hint='Start with who is doing the action. End with a period.', explanation='“The bird sings.” names the bird first, then what it does.'),
    Item('order_sun', 'write_order', 'Put these words in order: “shines / sun / The”. Type the sentence.', 'The sun shines.', hint='A sentence starts with a capital letter.', explanation='“The sun shines.” is the complete sentence.'),
    Item('punct_dog', 'write_punctuation', 'Rewrite this sentence with a capital letter and an ending mark: “the dog runs”', 'The dog runs.', hint='Capitalize the first word and put a period at the end.', explanation='“The dog runs.” has a capital T and a period.'),
    Item('punct_where', 'write_punctuation', 'Rewrite this question with a capital letter and an ending mark: “where is my hat”', 'Where is my hat?', hint='A question ends with a question mark.', explanation='“Where is my hat?” starts with a capital and ends with ?.'),
    Item('clarity_fish', 'write_clarity', 'Make a clear sentence from these words: “fish / The / swims / fast”.', 'The fish swims fast.', hint='Name the fish first, then say what it does.', explanation='“The fish swims fast.” keeps the idea in order.'),
    Item('clarity_fox', 'write_clarity', 'Make a clear sentence from these words: “fox / small / The / jumps”.', 'The small fox jumps.', hint='Put “small” before “fox.”', explanation='“The small fox jumps.” puts the describing word before fox.'),
    Item('count_three', 'math_count', 'You see ● ● ● at the {place}. How many dots are there?', '3', ('2', '3', '4'), 'Point to each dot once as you count.', 'There are 3 dots.'),
    Item('count_five', 'math_count', 'Count the stars: ★ ★ ★ ★ ★. How many are there?', '5', ('4', '5', '6'), 'Count one star at a time.', 'There are 5 stars.'),
    Item('add_two', 'math_add', 'You have 2 shells and find 3 more. How many shells now?', '5', ('4', '5', '6'), 'Count on three numbers from 2.', '2 + 3 = 5.'),
    Item('add_four', 'math_add', 'There are 4 lights at the {place}. Two more turn on. How many lights?', '6', ('5', '6', '7'), 'Start at 4 and count two more.', '4 + 2 = 6.'),
    Item('story_apples', 'math_story', 'A basket has 3 apples. Sam puts 4 more in. How many apples are in the basket?', '7', ('6', '7', '8'), 'Add the apples already there to the new apples.', '3 + 4 = 7.'),
    Item('story_birds', 'math_story', 'Two birds sit in a tree. Five more join them. How many birds are there?', '7', ('6', '7', '8'), 'Add the first group and the group that joined.', '2 + 5 = 7.'),
)
ITEM_BY_ID = {item.id: item for item in ITEMS}
ITEMS_BY_SKILL = {skill.id: tuple(item for item in ITEMS if item.skill_id == skill.id) for skill in SKILLS}


def normalize_answer(value: str) -> str:
    return ' '.join(str(value).strip().casefold().split())


def public_item(item: Item, world: str) -> dict:
    place = WORLDS[world]['place']
    return {
        'id': item.id,
        'prompt': item.prompt.format(place=place),
        'options': list(item.options),
        'response_kind': 'choice' if item.options else 'text',
    }
