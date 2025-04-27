# Using taskwarrior from Python

## Looking at tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> tasks = w.load_tasks()
    >>> tasks.keys()
    ['completed', 'pending']
    >>> type(tasks['pending'])
    <type 'list'>
    >>> type(tasks['pending'][0])
    <type 'dict'>
```

## Adding tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> w.task_add("Eat food")
    >>> w.task_add("Take a nap", priority="H", project="life", due="1359090000")
```

## Retrieving tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> w.get_task(id=5)
```

## Updating tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> id, task = w.get_task(id=14)
    >>> task['project'] = 'Updated project name'
    >>> w.task_update(task)
```

## Deleting tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> w.task_delete(id=3)
```

## Completing tasks

```
    >>> from taskw import TaskWarrior
    >>> w = TaskWarrior()
    >>> w.task_done(id=46)
```
