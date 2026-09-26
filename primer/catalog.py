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
    Item('sound_b', 'read_sounds', 'Which word begins with the /b/ sound?', 'boat', ('fish', 'boat', 'tree'), 'Say each word slowly and listen at the beginning.', 'Boat begins with /b/.'),
    Item('sound_f', 'read_sounds', 'Which word begins with the /f/ sound?', 'fish', ('moon', 'kite', 'fish'), 'Listen for the sound your teeth and lip make at the start.', 'Fish begins with /f/.'),
    Item('sentence_cat', 'read_sentences', 'Read: “The cat sits on the mat.” Where is the cat?', 'on the mat', ('on the mat', 'under the bed', 'in the tree'), 'Read the words after “sits.”', 'The sentence says the cat sits on the mat.'),
    Item('sentence_kite', 'read_sentences', 'Read: “Mia carries a red kite.” What does Mia carry?', 'a red kite', ('a red kite', 'a blue bag', 'a small book'), 'Look for the word after “carries.”', 'Mia carries a red kite.'),
    Item('sentence_door', 'read_sentences', 'Read: “Ben opens the blue door.” What color is the door?', 'blue', ('blue', 'green', 'yellow'), 'Look for the word just before “door.”', 'The sentence calls the door blue.'),
    Item('sentence_lamp', 'read_sentences', 'Read: “The lamp shines beside the book.” What is beside the book?', 'the lamp', ('the lamp', 'the cup', 'the hat'), 'Read the words before “shines.”', 'The lamp is beside the book.'),
    Item('passage_seed', 'read_passages', 'Read: “Lena plants a seed. She gives it water each day. A green leaf appears.” What helped the seed grow?', 'water', ('water', 'a shoe', 'a kite'), 'Read the second sentence again.', 'Lena watered the seed each day.'),
    Item('passage_map', 'read_passages', 'Read: “Noah finds a map. It shows a path to a bridge. He follows the path and sees the bridge.” What did the map show?', 'a path to a bridge', ('a path to a bridge', 'a boat', 'a mountain'), 'Look at the sentence that begins “It shows.”', 'The map showed a path to a bridge.'),
    Item('passage_rain', 'read_passages', 'Read: “Rain taps the roof. Jo gets an umbrella. Jo walks outside and stays dry.” What helped Jo stay dry?', 'an umbrella', ('an umbrella', 'a basket', 'a book'), 'Read the middle sentence again.', 'Jo took an umbrella outside.'),
    Item('passage_note', 'read_passages', 'Read: “A note says the gate opens at noon. Ari waits until noon. The gate opens.” When did the gate open?', 'at noon', ('at noon', 'at night', 'at dawn'), 'The first sentence gives the time.', 'The note said the gate opens at noon.'),
    Item('order_bird', 'write_order', 'Put these words in order: “sings / bird / The”. Type the sentence.', 'The bird sings.', hint='Start with who is doing the action. End with a period.', explanation='“The bird sings.” names the bird first, then what it does.'),
    Item('order_sun', 'write_order', 'Put these words in order: “shines / sun / The”. Type the sentence.', 'The sun shines.', hint='A sentence starts with a capital letter.', explanation='“The sun shines.” is the complete sentence.'),
    Item('order_ship', 'write_order', 'Put these words in order: “sails / ship / The”. Type the sentence.', 'The ship sails.', hint='Begin with “The ship” and finish with the action.', explanation='“The ship sails.” names the ship and then its action.'),
    Item('order_frog', 'write_order', 'Put these words in order: “frog / jumps / A”. Type the sentence.', 'A frog jumps.', hint='Begin with “A frog.” End with a period.', explanation='“A frog jumps.” is a complete sentence.'),
    Item('punct_dog', 'write_punctuation', 'Rewrite this sentence with a capital letter and an ending mark: “the dog runs”', 'The dog runs.', hint='Capitalize the first word and put a period at the end.', explanation='“The dog runs.” has a capital T and a period.'),
    Item('punct_where', 'write_punctuation', 'Rewrite this question with a capital letter and an ending mark: “where is my hat”', 'Where is my hat?', hint='A question ends with a question mark.', explanation='“Where is my hat?” starts with a capital and ends with ?.'),
    Item('punct_can', 'write_punctuation', 'Rewrite this question with a capital letter and an ending mark: “can we go now”', 'Can we go now?', hint='Start with a capital C and use the mark for a question.', explanation='“Can we go now?” begins with a capital and ends with ?.'),
    Item('punct_bird', 'write_punctuation', 'Rewrite this sentence with a capital letter and an ending mark: “the bird sings”', 'The bird sings.', hint='Start with a capital T and end a statement with a period.', explanation='“The bird sings.” uses a capital and a period.'),
    Item('clarity_fish', 'write_clarity', 'Make a clear sentence from these words: “fish / The / swims / fast”.', 'The fish swims fast.', hint='Name the fish first, then say what it does.', explanation='“The fish swims fast.” keeps the idea in order.'),
    Item('clarity_fox', 'write_clarity', 'Make a clear sentence from these words: “fox / small / The / jumps”.', 'The small fox jumps.', hint='Put “small” before “fox.”', explanation='“The small fox jumps.” puts the describing word before fox.'),
    Item('clarity_lamp', 'write_clarity', 'Make a clear sentence from these words: “lamp / bright / The / shines”.', 'The bright lamp shines.', hint='Put the describing word before “lamp.”', explanation='“The bright lamp shines.” places “bright” before the thing it describes.'),
    Item('clarity_boat', 'write_clarity', 'Make a clear sentence from these words: “boat / little / The / floats”.', 'The little boat floats.', hint='Name the boat before telling what it does.', explanation='“The little boat floats.” tells who or what and then the action.'),
    Item('count_three', 'math_count', 'You see ● ● ● at the {place}. How many dots are there?', '3', ('2', '3', '4'), 'Point to each dot once as you count.', 'There are 3 dots.'),
    Item('count_five', 'math_count', 'Count the stars: ★ ★ ★ ★ ★. How many are there?', '5', ('4', '5', '6'), 'Count one star at a time.', 'There are 5 stars.'),
    Item('count_four', 'math_count', 'Count the shells: ● ● ● ●. How many are there?', '4', ('3', '4', '5'), 'Touch each dot once as you count.', 'There are 4 dots.'),
    Item('count_six', 'math_count', 'Count the lights: ★ ★ ★ ★ ★ ★. How many are there?', '6', ('5', '6', '7'), 'Count one light at a time without skipping.', 'There are 6 lights.'),
    Item('add_two', 'math_add', 'You have 2 shells and find 3 more. How many shells now?', '5', ('4', '5', '6'), 'Count on three numbers from 2.', '2 + 3 = 5.'),
    Item('add_four', 'math_add', 'There are 4 lights at the {place}. Two more turn on. How many lights?', '6', ('5', '6', '7'), 'Start at 4 and count two more.', '4 + 2 = 6.'),
    Item('add_one', 'math_add', 'You have 5 stones and find 1 more. How many stones now?', '6', ('5', '6', '7'), 'Count one more after 5.', '5 + 1 = 6.'),
    Item('add_three', 'math_add', 'There are 3 lanterns. Three more light up. How many lanterns?', '6', ('5', '6', '7'), 'Make two groups of three and count them together.', '3 + 3 = 6.'),
    Item('story_apples', 'math_story', 'A basket has 3 apples. Sam puts 4 more in. How many apples are in the basket?', '7', ('6', '7', '8'), 'Add the apples already there to the new apples.', '3 + 4 = 7.'),
    Item('story_birds', 'math_story', 'Two birds sit in a tree. Five more join them. How many birds are there?', '7', ('6', '7', '8'), 'Add the first group and the group that joined.', '2 + 5 = 7.'),
    Item('story_books', 'math_story', 'There are 4 books on a shelf. 3 more are added. How many books are there?', '7', ('6', '7', '8'), 'Start with four and count three more.', '4 + 3 = 7.'),
    Item('story_seeds', 'math_story', 'Mina has 2 seeds. She finds 6 more. How many seeds does she have?', '8', ('7', '8', '9'), 'Add the two groups of seeds together.', '2 + 6 = 8.'),
)
ITEM_BY_ID = {item.id: item for item in ITEMS}
ITEMS_BY_SKILL = {skill.id: tuple(item for item in ITEMS if item.skill_id == skill.id) for skill in SKILLS}


def normalize_answer(value: str) -> str:
    return ' '.join(str(value).strip().casefold().split())


def grade_item(item: Item, response: str) -> tuple[bool, str]:
    """Grade bounded items without treating case or punctuation as irrelevant in writing.

    The result is deterministic and intentionally narrow. It does not purport
    to assess original writing or accept every valid paraphrase.
    """
    text = ' '.join(response.strip().split())
    expected = ' '.join(item.answer.split())
    if item.skill_id.startswith('write_'):
        if text == expected:
            return True, item.explanation
        if normalize_answer(text.rstrip('.?!')) == normalize_answer(expected.rstrip('.?!')):
            if not text or not text[0].isupper():
                return False, 'Begin the sentence with a capital letter.'
            return False, f'Check the ending mark. This sentence ends with {expected[-1]}'
        return False, item.hint
    correct = normalize_answer(text) == normalize_answer(expected)
    return correct, item.explanation if correct else item.hint


def public_item(item: Item, world: str) -> dict:
    place = WORLDS[world]['place']
    return {
        'id': item.id,
        'prompt': item.prompt.format(place=place),
        'options': list(item.options),
        'response_kind': 'choice' if item.options else 'text',
    }
